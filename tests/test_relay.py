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


def make_db(path: str, private: bool = False, dm_online: bool = True,
            legacy: bool = False, emergency: bool = False) -> None:
    """``legacy``: schema del modulo vecchio, senza vc_dm_pos (il DM stava in vc_positions)."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE vc_positions(
            cdkey TEXT PRIMARY KEY, playername TEXT, charname TEXT, area TEXT,
            x REAL, y REAL, z REAL, facing REAL, seq INTEGER, range_mode INTEGER);
        INSERT INTO vc_positions VALUES('KTEST', 'Tester', 'Aribeth', 'attracco', 10, 20, 0, 90, 1, 1);
        INSERT INTO vc_positions VALUES('KAMICO','Amico',  'Drizzt',  'attracco', 14, 20, 0,  0, 1, 2);
    """)
    if legacy:
        c.execute("INSERT INTO vc_positions VALUES('KDM','Master','DM Voce','attracco',5,5,0,0,1,1)")
    else:
        c.executescript("""
            CREATE TABLE vc_dm_pos(
                cdkey TEXT PRIMARY KEY, playername TEXT, charname TEXT, area TEXT,
                x REAL, y REAL, z REAL, facing REAL, ip TEXT, range_mode INTEGER, seq INTEGER);
            INSERT INTO vc_dm_pos VALUES('KDM2','Aiuto','Secondo DM','attracco',30,30,0,0,'',1,1);
        """)
        if dm_online:
            c.execute("INSERT INTO vc_dm_pos VALUES('KDM','Master','DM Voce','attracco',5,5,0,0,'',?,1)",
                      (positions.EMERGENCY_MODE if emergency else 0,))
    if private:
        # "Isola" (allucinazione): il DM lo sente solo Amico
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
    dm_online = True
    legacy = False
    emergency = False
    token = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "nwn_voice.sqlite3")
        make_db(self.db, private=self.private, dm_online=self.dm_online, legacy=self.legacy,
                emergency=self.emergency)
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
        # ognuno compare col nome account E col nome personaggio (DM compresi)
        self.assertEqual(names, {"Tester", "Aribeth", "Amico", "Drizzt",
                                 "Master", "DM Voce", "Aiuto", "Secondo DM"})
        amico = next(r for r in roster if r[0] == "Amico")
        self.assertEqual(amico[5], 2)   # urla
        self.assertFalse(any(r[1] == positions.PRIV_MUTE_AREA for r in roster))


class TestDungeonMaster(RelayCase):
    def test_dm_heard_like_a_player(self):
        """Il DM e' una voce come le altre: sua posizione, sua portata (qui sussurra)."""
        roster = self.wait_for(self.connect("Tester").read_roster)
        self.assertEqual(next(r for r in roster if r[0] == "Master"),
                         ("Master", "attracco", 5.0, 5.0, 0.0, 0))

    def test_dm_hears_around_himself(self):
        dm = self.connect("Master")
        st = self.wait_for(dm.poll)
        self.assertIsNotNone(st, "l'app del DM deve risultare in gioco")
        self.assertEqual((st.area, st.x, st.y), ("attracco", 5.0, 5.0))
        roster = self.wait_for(dm.read_roster)
        # se stesso (per la distanza da chi parla) in testa, poi tutti gli altri
        self.assertEqual(roster[0], ("Master", "attracco", 5.0, 5.0, 0.0, 0))
        self.assertEqual({r[0] for r in roster},
                         {"Master", "DM Voce", "Aiuto", "Secondo DM",
                          "Tester", "Aribeth", "Amico", "Drizzt"})


class TestEmergency(RelayCase):
    emergency = True

    def test_dm_in_emergency_heard_by_everyone(self):
        """Emergenza: il DM e' fuori dal roster degli altri -> volume pieno, ovunque."""
        names = {r[0] for r in self.wait_for(self.connect("Tester").read_roster)}
        self.assertFalse({"Master", "DM Voce"} & names)
        self.assertIn("Aiuto", names, "l'altro DM resta normale")
        # lui invece continua a sentire chi ha intorno
        self.assertEqual(self.wait_for(self.connect("Master").read_roster)[0][:3],
                         ("Master", "attracco", 5.0))


class TestPrivateVoice(RelayCase):
    private = True

    def test_only_members_hear_dm_by_distance(self):
        escluso = self.connect("Tester")
        scelto = self.connect("Amico")
        altro_dm = self.connect("Aiuto")
        r_escluso = self.wait_for(escluso.read_roster)
        r_scelto = self.wait_for(scelto.read_roster)
        r_dm = self.wait_for(altro_dm.read_roster)
        master = ("Master", "attracco", 5.0, 5.0, 0.0, 0)
        # il plugin usa la PRIMA voce col nome giusto
        first = lambda roster, n: next(r for r in roster if r[0] == n)  # noqa: E731
        self.assertEqual({r[0] for r in r_escluso if r[1] == positions.PRIV_MUTE_AREA},
                         {"Master", "DM Voce"}, "chi non e' selezionato non deve sentire il DM")
        self.assertEqual(first(r_escluso, "Master")[1], positions.PRIV_MUTE_AREA)
        self.assertEqual(first(r_scelto, "Master"), master,
                         "chi e' selezionato lo sente con distanza e portata")
        self.assertEqual(first(r_dm, "Master"), master, "gli altri DM lo sentono come sempre")
        self.assertFalse(any(r[1] == positions.PRIV_MUTE_AREA for r in r_scelto + r_dm))


class TestPrivateVoiceEmergency(RelayCase):
    private = True
    emergency = True

    def test_emergency_wins_over_isola(self):
        roster = self.wait_for(self.connect("Tester").read_roster)
        self.assertFalse({"Master", "DM Voce"} & {r[0] for r in roster},
                         "in emergenza lo sentono tutti, anche i non selezionati")


class TestPrivateVoiceDmGone(RelayCase):
    private = True
    dm_online = False

    def test_session_of_absent_dm_ignored(self):
        """Isola rimasta accesa ma il DM non c'e' piu': nessuno deve restare mutato."""
        roster = self.wait_for(self.connect("Tester").read_roster)
        self.assertFalse(any(r[1] == positions.PRIV_MUTE_AREA for r in roster))


class TestPrivateVoiceLegacyModule(RelayCase):
    private = True
    legacy = True

    def test_old_module_still_isolates(self):
        """Relay nuovo con il modulo vecchio (senza vc_dm_pos): Isola funziona come prima."""
        roster = self.wait_for(self.connect("Tester").read_roster)
        self.assertEqual({r[0] for r in roster if r[1] == positions.PRIV_MUTE_AREA},
                         {"Master", "DM Voce"})


class TestGhosts(unittest.TestCase):
    def test_frozen_row_kept_then_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "nwn_voice.sqlite3")
            make_db(db)
            p = positions.SqlFileProvider(db_path=db, player="Tester", roster_stale_after=0.3)
            p.open()
            try:
                self.assertIn("Amico", {r[0] for r in p.read_roster()})
                time.sleep(0.15)
                self.assertIn("Amico", {r[0] for r in p.read_roster()},
                              "una riga ferma da poco (caricamento) resta")
                time.sleep(0.3)
                self.assertNotIn("Amico", {r[0] for r in p.read_roster()},
                                 "una riga ferma da troppo e' un fantasma")
            finally:
                p.close()
        self.assertEqual(positions.ROSTER_STALE_AFTER, 30.0)


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

    def test_icon_written_for_dm(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "nwn_voice.sqlite3")
            make_db(db)
            w = server._TalkWriter(db)
            w.set_talking("Master", True)
            w.close()
            c = sqlite3.connect(db)
            rows = c.execute("SELECT cdkey, talking FROM vc_client").fetchall()
            c.close()
            self.assertEqual(rows, [("KDM", 1)])


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
