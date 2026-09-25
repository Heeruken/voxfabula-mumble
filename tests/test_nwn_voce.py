"""Test senza Docker, senza Mumble e senza NWN:  python -m unittest -v

- ponte completo contro un relay FINTO in locale (stesso protocollo del vero);
- file di impostazioni del nostro Mumble;
- ricerca dei processi per percorso.
"""

from __future__ import annotations

import json
import mmap
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nwn_voce import bridge, mumble_link, net_protocol as proto, shared_state, winproc  # noqa: E402
from nwn_voce.relay_client import RelayClient  # noqa: E402
from nwn_voce.state import PlayerState  # noqa: E402


class FakeRelay:
    """Relay finto: accetta un client, legge l'hello, manda stato + roster."""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.hello = None
        self.in_game = threading.Event()
        self.in_game.set()
        self.stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.srv.accept()
        self.hello = json.loads(conn.makefile("rb").readline())
        st = PlayerState(x=10.0, y=20.0, z=0.0, facing=90.0, area="attracco",
                         server="nwnvoce", name="Tester", identity="Tester")
        roster = [("Tester", "attracco", 10.0, 20.0, 0.0, 1),
                  ("Amico", "attracco", 14.0, 20.0, 0.0, 2)]
        while not self.stop.is_set():
            try:
                conn.sendall(proto.encode_state(st) if self.in_game.is_set() else proto.encode_idle())
                conn.sendall(proto.encode_roster(roster))
            except OSError:
                break
            time.sleep(0.05)
        conn.close()
        self.srv.close()


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def read_roster_shm():
    h = struct.Struct("<IIII")
    e = struct.Struct("<32s32sfffi")
    mm = mmap.mmap(-1, shared_state.SIZE, tagname=shared_state.TAG, access=mmap.ACCESS_READ)
    try:
        magic, seq, count, _ = h.unpack(mm[0:h.size])
        out = []
        for i in range(count):
            off = h.size + i * e.size
            n, a, x, y, z, m = e.unpack(mm[off:off + e.size])
            out.append((n.rstrip(b"\0").decode(), a.rstrip(b"\0").decode(), x, y, z, m))
        return magic, out
    finally:
        mm.close()


class TestBridgeEndToEnd(unittest.TestCase):
    def test_relay_to_shared_memory(self):
        relay = FakeRelay()
        client = RelayClient("127.0.0.1", relay.port, player="Tester")
        client.open()
        stop = threading.Event()
        states = []
        t = threading.Thread(target=bridge.run, args=(client, stop),
                             kwargs={"on_live": states.append}, daemon=True)
        t.start()
        try:
            deadline = time.time() + 5
            while True not in states and time.time() < deadline:
                time.sleep(0.05)
            self.assertIn(True, states, "il ponte non e' mai diventato attivo")
            self.assertEqual(relay.hello.get("hello"), "Tester")
            self.assertTrue(client.connected)

            time.sleep(0.3)  # lascia scrivere il roster
            magic, roster = read_roster_shm()
            self.assertEqual(magic, shared_state.MAGIC)
            self.assertEqual([r[0] for r in roster], ["Tester", "Amico"])
            self.assertEqual(roster[1][5], 2)  # Amico sta urlando

            frame = mumble_link.read_current()
            self.assertEqual(frame["version"], 2)
            self.assertIn(b"attracco", frame["context"])

            relay.in_game.clear()  # il personaggio esce dal gioco
            deadline = time.time() + 5
            while states[-1] is not False and time.time() < deadline:
                time.sleep(0.05)
            self.assertIs(states[-1], False, "uscito dal gioco ma il ponte resta attivo")
        finally:
            stop.set()
            t.join(3)
            client.close()
            relay.stop.set()
        self.assertFalse(t.is_alive(), "il ponte non si ferma")


class TestMumbleConfig(unittest.TestCase):
    def test_config_is_ours_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("APPDATA")
            os.environ["APPDATA"] = tmp
            try:
                from nwn_voce import mumble_config, paths
                mdir = os.path.join(tmp, "mumble")
                os.makedirs(os.path.join(mdir, "plugins"))
                path = mumble_config.write_config(mdir, input_device="{0.0.1.00000000}.{x}")
                self.assertTrue(path.startswith(tmp), "deve scrivere solo nella NOSTRA cartella")
                d = _read(path)
                self.assertFalse(d["positional_audio"]["enable_positional_audio"])
                self.assertEqual(d["ui"]["quit_behavior"], "AlwaysQuit")
                self.assertTrue(d["misc"]["database_location"].startswith(tmp.replace("\\", "/")))
                self.assertTrue(os.path.exists(paths.mumble_database_file()),
                                "il database deve esistere, o Mumble si blocca su 'File not found'")
                self.assertFalse(d["misc"]["check_for_updates"])
                vc = os.path.join(mdir, "plugins", "vc_range.dll")
                self.assertTrue(d["plugins"][mumble_config.plugin_key(vc)]["enabled"])
                self.assertEqual(d["audio_backend"]["wasapi_input"], "{0.0.1.00000000}.{x}")

                d["certificate"] = "CERT-GENERATO-DA-MUMBLE"   # Mumble lo aggiunge al 1o avvio
                _write(path, d)
                mumble_config.write_config(mdir)                 # "Predefinito" = togli la scelta
                d2 = _read(path)
                self.assertEqual(d2["certificate"], "CERT-GENERATO-DA-MUMBLE", "il certificato va conservato")
                self.assertNotIn("wasapi_input", d2["audio_backend"])
                self.assertEqual(paths.mumble_settings_file(), path)
            finally:
                if old is None:
                    os.environ.pop("APPDATA", None)
                else:
                    os.environ["APPDATA"] = old


class TestMumbleConfigRepair(unittest.TestCase):
    def test_every_plugin_entry_is_complete(self):
        """Crash reale del 25/09: una voce di plugin senza 'positional_data_enabled'
        fa lanciare a Mumble un'eccezione JSON all'avvio. Simula il PC reale: copia
        vecchia di vc_range in %APPDATA%\\Mumble e una config gia' rotta su disco."""
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("APPDATA")
            os.environ["APPDATA"] = tmp
            try:
                from nwn_voce import mumble_config, paths
                os.makedirs(os.path.join(tmp, "Mumble", "Mumble", "Plugins"))
                open(os.path.join(tmp, "Mumble", "Mumble", "Plugins", "vc_range.dll"), "wb").close()
                broken = "C:/Users/x/AppData/Roaming/Mumble/Mumble/Plugins/vc_range.dll"
                _write(paths.mumble_settings_file(),
                       {"plugins": {mumble_config.plugin_key(broken): {"path": broken, "enabled": False},
                                    "rotto": "non-un-oggetto"}})
                mdir = os.path.join(tmp, "mumble")
                os.makedirs(os.path.join(mdir, "plugins"))
                d = _read(mumble_config.write_config(mdir))
                for key, entry in d["plugins"].items():
                    for field in ("enabled", "positional_data_enabled",
                                  "keyboard_monitoring_allowed", "path"):
                        self.assertIn(field, entry, f"plugin {key}: manca {field}")
                self.assertNotIn("rotto", d["plugins"])
                self.assertFalse(d["plugins"][mumble_config.plugin_key(broken)]["enabled"])
            finally:
                if old is None:
                    os.environ.pop("APPDATA", None)
                else:
                    os.environ["APPDATA"] = old


class TestApiSurface(unittest.TestCase):
    def test_only_methods_are_public(self):
        """pywebview esplora ricorsivamente gli attributi PUBBLICI dell'Api: se ci
        finisce la finestra, all'avvio si blocca tutto (pagina scollegata, chiusura
        che non risponde). Pubblici devono essere solo i metodi per la pagina."""
        from nwn_voce.main import Api
        api = Api()
        try:
            for name in dir(api):
                if name.startswith("_"):
                    continue
                self.assertTrue(callable(getattr(api, name)),
                                f"attributo pubblico non-metodo: {name} (rinominalo _{name})")
        finally:
            api._engine.stop(quiet=True)


class TestWinProc(unittest.TestCase):
    def test_finds_only_exact_image(self):
        pids = winproc.pids_for_image(sys.executable)
        self.assertIn(os.getpid(), pids)
        self.assertEqual(winproc.pids_for_image(sys.executable + ".nonesiste"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
