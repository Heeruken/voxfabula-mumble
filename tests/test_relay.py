"""Test del relay senza Docker e senza Mumble: database finto con lo stesso
schema che scrive vc_voice.nss, relay vero in un thread, app vera come client."""

from __future__ import annotations

import os
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nwn_voce.relay_client import RelayClient  # noqa: E402
from relay import positions, server  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_db(path: str, private: bool = False) -> None:
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE vc_positions(
            cdkey TEXT PRIMARY KEY, playername TEXT, charname TEXT, area TEXT,
            x REAL, y REAL, z REAL, facing REAL, seq INTEGER, range_mode INTEGER);
        INSERT INTO vc_positions VALUES('KTEST', 'Tester', 'Aribeth', 'attracco', 10, 20, 0, 90, 1, 1);
        INSERT INTO vc_positions VALUES('KAMICO','Amico',  'Drizzt',  'attracco', 14, 20, 0,  0, 1, 2);
        INSERT INTO vc_positions VALUES('KDM',   'Master', 'DM Voce', 'attracco',  5,  5, 0,  0, 1, 1);
    """)
    if private:
        # voce privata DM "Appari solo a": il DM parla solo ad Amico
        c.executescript("""
            CREATE TABLE vc_priv_session(spk TEXT, spk_p TEXT, spk_c TEXT, active INTEGER);
            CREATE TABLE vc_priv_member(spk TEXT, mem TEXT);
            INSERT INTO vc_priv_session VALUES('KDM', 'Master', 'DM Voce', 1);
            INSERT INTO vc_priv_member VALUES('KDM', 'KAMICO');
        """)
    c.commit()
    c.close()


class RelayCase(unittest.TestCase):
    private = False
    token = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "nwn_voice.sqlite3")
        make_db(self.db, private=self.private)
        self.port = free_port()
        self.stop = threading.Event()
        ready = threading.Event()
        self.thread = threading.Thread(
            target=server.serve, daemon=True,
            kwargs=dict(db_path=self.db, host="127.0.0.1", port=self.port,
                        server_id="nwnvoce", token=self.token, stop=self.stop,
                        ready=ready, talk=False))
        self.thread.start()
        self.assertTrue(ready.wait(5), "il relay non si e' messo in ascolto")
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.stop.set()
        self.thread.join(5)
        self.tmp.cleanup()

    def connect(self, player, token=None) -> RelayClient:
        c = RelayClient("127.0.0.1", self.port, player=player, token=token)
        c.open()
        self.clients.append(c)
        return c

    def wait_for(self, fn, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.05)
        return fn()


class TestPositions(RelayCase):
    def test_own_state_and_roster(self):
        c = self.connect("Tester")
        st = self.wait_for(c.poll)
        self.assertIsNotNone(st, "nessuna posizione ricevuta dal relay")
        self.assertEqual((st.area, st.x, st.y, st.server), ("attracco", 10.0, 20.0, "nwnvoce"))
        roster = self.wait_for(c.read_roster)
        names = {r[0] for r in roster}
        # ognuno compare col nome account E col nome personaggio
        self.assertTrue({"Tester", "Aribeth", "Amico", "Drizzt", "Master", "DM Voce"} <= names)
        amico = next(r for r in roster if r[0] == "Amico")
        self.assertEqual(amico[5], 2)   # urla
        self.assertFalse(any(r[1] == positions.PRIV_MUTE_AREA for r in roster))


class TestPrivateVoice(RelayCase):
    private = True

    def test_dm_muted_only_for_non_members(self):
        escluso = self.connect("Tester")
        scelto = self.connect("Amico")
        r_escluso = self.wait_for(escluso.read_roster)
        r_scelto = self.wait_for(scelto.read_roster)
        muted_for_escluso = {r[0] for r in r_escluso if r[1] == positions.PRIV_MUTE_AREA}
        self.assertEqual(muted_for_escluso, {"Master", "DM Voce"},
                         "chi non e' selezionato non deve sentire il DM")
        self.assertFalse(any(r[1] == positions.PRIV_MUTE_AREA for r in r_scelto),
                         "chi e' selezionato deve sentire il DM")


class TestToken(RelayCase):
    token = "segreto"

    def test_wrong_token_rejected(self):
        c = self.connect("Tester", token="sbagliato")
        self.assertEqual(self.wait_for(lambda: c.rejected), "bad token")


class TestTalkWriter(unittest.TestCase):
    def test_icon_written_even_with_mumble_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "nwn_voice.sqlite3")
            make_db(db)
            w = server._TalkWriter(db)
            w.set_talking("Tester2", True)      # Mumble aggiunge "2" se il nome e' occupato
            w.set_talking("Sconosciuto", True)  # non in gioco: niente riga
            w.close()
            c = sqlite3.connect(db)
            rows = c.execute("SELECT cdkey, talking FROM vc_client").fetchall()
            c.close()
            self.assertEqual(rows, [("KTEST", 1)])


class TestDockerfile(unittest.TestCase):
    def test_copied_files_exist(self):
        """Se un file viene rinominato, la build dell'immagine si romperebbe."""
        with open(os.path.join(ROOT, "relay", "Dockerfile"), encoding="utf-8") as f:
            copies = [line.split()[1:-1] for line in f if line.startswith("COPY ")]
        import glob
        for srcs in copies:
            for src in srcs:
                self.assertTrue(glob.glob(os.path.join(ROOT, src)), f"manca {src}")
        self.assertTrue(any("net_protocol.py" in s for srcs in copies for s in srcs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
