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
                self.assertFalse(d["update"]["check_for_updates"])      # sezione verificata dal vivo
                self.assertNotIn("check_for_updates", d["misc"])
                self.assertNotIn("overlay", d)
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


class TempAppData(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old
        self._tmp.cleanup()


class TestRenameCarryOver(TempAppData):
    """Col nome nuovo ("Vox Fabula Voice") le impostazioni della versione di prova
    ("NWN Voce") vengono copiate, una volta sola, e la cartella vecchia resta."""

    def test_settings_copied_once(self):
        from nwn_voce import OLD_APP_NAME, paths, settings
        old = os.path.join(self._tmp.name, OLD_APP_NAME)
        os.makedirs(old)
        with open(os.path.join(old, "settings.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "Hiruken", "host": "127.0.0.1"}, fh)
        with open(os.path.join(old, "nwnvoce.log"), "w") as fh:
            fh.write("vecchio log")
        self.assertEqual(settings.load().get("name"), "Hiruken")
        self.assertTrue(os.path.isfile(os.path.join(old, "settings.json")))       # copiata, non spostata
        self.assertFalse(os.path.exists(os.path.join(paths.data_dir(), "nwnvoce.log")))
        # la cartella nuova esiste gia': un cambio nel vecchio non la tocca piu'
        with open(os.path.join(old, "settings.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "Altro"}, fh)
        self.assertEqual(settings.load().get("name"), "Hiruken")

    def test_fresh_install_without_old_folder(self):
        from nwn_voce import settings
        self.assertIsNone(settings.load().get("name"))


class TestServerAddress(TempAppData):
    """I giocatori scrivono solo il nome: il server e' voice.voxfabula.it, salvo
    'Server (avanzato)'. L'IP scritto a mano nelle versioni vecchie si ignora."""

    def test_default_and_old_host_ignored(self):
        from nwn_voce import DEFAULT_SERVER, settings
        from nwn_voce.main import server_address
        self.assertEqual(server_address(), DEFAULT_SERVER)
        settings.update(host="79.25.34.204")               # vecchio campo: IP di casa di un momento
        self.assertEqual(server_address(), "voice.voxfabula.it")

    def test_advanced_override(self):
        from nwn_voce import settings
        from nwn_voce.main import server_address
        settings.update(server="127.0.0.1")
        self.assertEqual(server_address(), "127.0.0.1")
        settings.update(server="non valido!")               # scritto a mano male nel file: si ignora
        self.assertEqual(server_address(), "voice.voxfabula.it")

    def test_start_uses_server_and_save_validates(self):
        from nwn_voce.main import Api
        api = Api()
        calls = []
        try:
            api._engine.start = lambda host, name: calls.append((host, name))
            self.assertEqual(api.save_server("300.1.1.1"), {"ok": False})
            self.assertEqual(api.save_server(" 127.0.0.1 "), {"ok": True})
            self.assertEqual(api.start(" Hiruken "), {"ok": True})
            self.assertEqual(api.start(""), {"ok": False})
            self.assertEqual(api.save_server(""), {"ok": True})
            api.start("Amico")
            for _ in range(50):
                if len(calls) == 2:
                    break
                time.sleep(0.02)
            self.assertEqual(calls, [("127.0.0.1", "Hiruken"), ("voice.voxfabula.it", "Amico")])
            self.assertNotIn("host", api.get_settings())
            self.assertEqual(api.get_settings()["default_server"], "voice.voxfabula.it")
        finally:
            api._engine.stop(quiet=True)


class TestPushToTalk(TempAppData):
    # scritti da Mumble stesso (test dal vivo del 25/09)
    MUMBLE_CAPSLOCK = "AAAEAAAAAAAOSW5wdXRLZXlib2FyZAAAADo="
    MUMBLE_MOUSE5 = "AAAEAAAAAAALSW5wdXRNb3VzZQAAAAAF"

    def test_encoding_matches_what_mumble_writes(self):
        from nwn_voce import mumble_keys
        self.assertEqual(mumble_keys.encode({"kind": "keyboard", "code": "CapsLock"}),
                         (self.MUMBLE_CAPSLOCK, True))
        self.assertEqual(mumble_keys.encode({"kind": "mouse", "button": 4}), (self.MUMBLE_MOUSE5, False))
        self.assertEqual(mumble_keys.KEYBOARD_SCANCODES["KeyV"], 0x2F)
        self.assertEqual(mumble_keys.KEYBOARD_SCANCODES["Space"], 0x39)
        self.assertEqual(mumble_keys.KEYBOARD_SCANCODES["F12"], 0x58)
        self.assertFalse(mumble_keys.encode({"kind": "keyboard", "code": "KeyV"})[1],
                         "le lettere non vanno 'mangiate': non si potrebbero piu' scrivere")
        with self.assertRaises(ValueError):
            mumble_keys.encode({"kind": "keyboard", "code": "ArrowUp"})

    def test_config_default_is_capslock_ptt(self):
        from nwn_voce import mumble_config
        mdir = os.path.join(self._tmp.name, "mumble")
        os.makedirs(os.path.join(mdir, "plugins"))
        d = _read(mumble_config.write_config(mdir))
        self.assertEqual(d["audio"]["transmit_mode"], "PTT")
        ptt = [s for s in d["shortcuts"]["defined"] if s["index"] == 1]
        self.assertEqual(ptt, [{"buttons": [self.MUMBLE_CAPSLOCK], "data": "AAAAAAE=",
                                "index": 1, "suppress": True}])

    def test_config_vad_and_key_change(self):
        from nwn_voce import mumble_config
        mdir = os.path.join(self._tmp.name, "mumble")
        os.makedirs(os.path.join(mdir, "plugins"))
        path = mumble_config.write_config(mdir)
        d = _read(path)
        d["shortcuts"]["defined"].append({"buttons": [], "data": "AAAAAAE=", "index": 7, "suppress": False})
        _write(path, d)
        d = _read(mumble_config.write_config(mdir, transmit="vad", ptt_key={"kind": "mouse", "button": 4}))
        self.assertEqual(d["audio"]["transmit_mode"], "VAD")
        indexes = sorted(s["index"] for s in d["shortcuts"]["defined"])
        self.assertEqual(indexes, [1, 7], "un solo push-to-talk, le altre scorciatoie restano")
        d = _read(mumble_config.write_config(mdir, ptt_key={"kind": "keyboard", "code": "ArrowUp"}))
        ptt = next(s for s in d["shortcuts"]["defined"] if s["index"] == 1)
        self.assertEqual(ptt["buttons"], [self.MUMBLE_CAPSLOCK], "tasto non supportato -> predefinito")

    def test_ui_key_list_matches_python(self):
        import re
        from nwn_voce import mumble_keys
        html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "nwn_voce", "web", "index.html"), encoding="utf-8").read()
        block = html[html.index("const PTT_CODES"):html.index("const DEFAULT_KEY")]
        js = set(re.findall(r'"([A-Za-z]+\d*)"', block))
        js |= {"Key" + c for c in "QWERTYUIOPASDFGHJKLZXCVBNM"}
        js |= {f"F{n}" for n in range(1, 13)} | {f"Numpad{n}" for n in range(10)}
        js -= {"QWERTYUIOPASDFGHJKLZXCVBNM", "Key", "F", "Numpad"}   # pezzi di codice JS, non tasti
        self.assertEqual(js, set(mumble_keys.KEYBOARD_SCANCODES))


class TestServerCertificate(TempAppData):
    def rows(self):
        import sqlite3
        from nwn_voce import paths
        c = sqlite3.connect(paths.mumble_database_file())
        try:
            return c.execute("SELECT hostname, port, digest FROM cert").fetchall()
        finally:
            c.close()

    def test_seeded_once_on_empty_database(self):
        from nwn_voce import mumble_config
        self.assertTrue(mumble_config.seed_server_cert("play.esempio.it"))
        self.assertFalse(mumble_config.seed_server_cert("play.esempio.it"))   # niente doppioni
        self.assertEqual(self.rows(), [("play.esempio.it", 64738, mumble_config.SERVER_CERT_SHA1)])

    def test_existing_choice_is_respected(self):
        import sqlite3
        from nwn_voce import mumble_config, paths
        mumble_config.seed_server_cert("h", digest="aaaa")
        self.assertFalse(mumble_config.seed_server_cert("h"))
        self.assertEqual(self.rows(), [("h", 64738, "aaaa")])

    def test_digest_format(self):
        from nwn_voce import mumble_config
        self.assertRegex(mumble_config.SERVER_CERT_SHA1, r"^[0-9a-f]{40}$")

    def test_replace_updates_changed_certificate(self):
        from nwn_voce import mumble_config
        mumble_config.seed_server_cert("h", digest="vecchia")
        self.assertTrue(mumble_config.seed_server_cert("h", digest="nuova", replace=True))
        self.assertFalse(mumble_config.seed_server_cert("h", digest="nuova", replace=True))
        self.assertEqual(self.rows(), [("h", 64738, "nuova")])

    def test_unreachable_server_falls_back_to_known_digest(self):
        from nwn_voce import mumble_config
        port = _free_port()          # nessuno in ascolto
        self.assertIsNone(mumble_config.fetch_server_digest("127.0.0.1", port, timeout=2))
        self.assertEqual(mumble_config.accept_server_cert("127.0.0.1", port), "known")
        self.assertEqual(self.rows(), [("127.0.0.1", port, mumble_config.SERVER_CERT_SHA1)])

    def test_live_certificate_read_from_tls_server(self):
        """Server TLS finto con un certificato usa-e-getta: l'app registra la SUA
        impronta (SHA-1 del DER, come Mumble), anche sopra una voce vecchia."""
        import hashlib
        import ssl
        from nwn_voce import mumble_config
        cert, key, der = _self_signed(self._tmp.name)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        port = srv.getsockname()[1]

        def serve():
            for _ in range(2):
                try:
                    conn, _ = srv.accept()
                    with ctx.wrap_socket(conn, server_side=True) as tls:
                        tls.recv(1)
                except OSError:
                    pass
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        try:
            expected = hashlib.sha1(der).hexdigest()
            self.assertEqual(mumble_config.fetch_server_digest("127.0.0.1", port), expected)
            mumble_config.seed_server_cert("127.0.0.1", port, digest="vecchia")
            self.assertEqual(mumble_config.accept_server_cert("127.0.0.1", port), "live")
            self.assertEqual(self.rows(), [("127.0.0.1", port, expected)])
        finally:
            srv.close()
            t.join(5)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _self_signed(folder: str):
    """Certificato auto-firmato di prova (come quello del server Mumble)."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Murmur Autogenerated Certificate v2")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    cert_path, key_path = os.path.join(folder, "c.pem"), os.path.join(folder, "k.pem")
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))
    return cert_path, key_path, cert.public_bytes(serialization.Encoding.DER)


class TestCrashDumpStash(TempAppData):
    def test_old_dump_moved_aside(self):
        from nwn_voce import paths
        from nwn_voce.engine import Engine
        dmp = os.path.join(paths.data_dir(), "mumble.dmp")
        open(dmp, "wb").close()
        Engine._stash_crash_dump()
        self.assertFalse(os.path.exists(dmp), "Mumble mostrerebbe il 'Rapporto errori'")
        self.assertEqual(len(os.listdir(os.path.join(paths.data_dir(), "crash"))), 1)


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
