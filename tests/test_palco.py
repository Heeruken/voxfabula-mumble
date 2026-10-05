"""Test del palco (scene interattive) lato app: niente finestre, l'overlay e' finto
(un thread che si collega alla porta come farebbe il processo --palco)."""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nwn_voce import palco, paths, sigillo  # noqa: E402


class FintoClient:
    def __init__(self):
        self.ev = []

    def send_scena_ev(self, sid, ev, dati=None):
        self.ev.append((sid, ev, dati or {}))
        return True


class Registro:
    def __init__(self):
        self.righe = []

    def scrivi(self, chiave, **dati):
        self.righe.append((chiave, dati))


class FintoProc:
    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def finto_overlay(cosa_fa):
    """Sostituisce subprocess.Popen: si collega come l'overlay vero e fa ``cosa_fa(sock, righe, avvio)``."""
    visto = {}

    def popen(cmd, **_k):
        porta, segreto = int(cmd[-2]), cmd[-1]

        def gira():
            s = socket.create_connection(("127.0.0.1", porta))
            s.sendall((json.dumps({"segreto": segreto}) + "\n").encode())
            righe = s.makefile("rb")
            avvio = json.loads(righe.readline())
            visto["avvio"] = avvio
            cosa_fa(s, righe, avvio)
            s.close()
        threading.Thread(target=gira, daemon=True).start()
        return FintoProc()
    return popen, visto


def manda(s, d):
    s.sendall((json.dumps(d) + "\n").encode())


class PalcoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(paths, "data_dir", return_value=self.tmp.name)
        self.p.start()
        self.reg = Registro()
        self.palco = palco.Palco(self.reg)
        self.client = FintoClient()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def apri(self, popen, tipo="esamina", dati=None, sid="ab12"):
        with mock.patch.object(palco.subprocess, "Popen", side_effect=popen):
            self.palco._apri(sid, tipo, dati if dati is not None else {}, self.client)

    def test_scena_eventi_ed_esito(self):
        def fa(s, righe, avvio):
            manda(s, {"ev": "pronta", "dati": {}})
            manda(s, {"ev": "azione", "dati": {"id": "indaga"}})
            manda(s, {"ev": "chiusa", "dati": {"esito": "esc"}})
        popen, visto = finto_overlay(fa)
        self.apri(popen, dati={"titolo": "Coppa"})
        self.assertEqual(visto["avvio"]["tipo"], "esamina")
        self.assertEqual(visto["avvio"]["dati"], {"titolo": "Coppa"})
        self.assertEqual(self.client.ev, [("ab12", "pronta", {}), ("ab12", "azione", {"id": "indaga"}),
                                          ("ab12", "chiusa", {"esito": "esc"})])
        self.assertEqual([r[0] for r in self.reg.righe], ["scena_aperta", "scena_chiusa"])

    def test_messaggi_del_server_arrivano_all_overlay(self):
        arrivati = []

        def fa(s, righe, avvio):
            manda(s, {"ev": "pronta", "dati": {}})
            arrivati.append(json.loads(righe.readline()))
            manda(s, {"ev": "chiusa", "dati": {"esito": "chiusa"}})
        popen, _ = finto_overlay(fa)
        t = threading.Thread(target=self.apri, args=(popen,))
        t.start()
        fine = time.time() + 5
        while not self.client.ev and time.time() < fine:
            time.sleep(0.02)
        self.palco.evento("msg", "ab12", "", {"tipo": "tiro", "d20": 17}, self.client)
        self.palco.evento("msg", "zz99", "", {"tipo": "altra scena"}, self.client)   # non e' la sua
        t.join(5)
        self.assertEqual(arrivati, [{"msg": {"tipo": "tiro", "d20": 17}}])

    def test_tipo_sconosciuto(self):
        self.apri(lambda *a, **k: self.fail("non doveva partire"), tipo="nonesiste")
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "sconosciuta"})])

    def test_interruttore_spento(self):
        with mock.patch.object(palco, "palco_attivo", return_value=False):
            self.apri(lambda *a, **k: self.fail("non doveva partire"))
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "disattivato"})])

    def test_oggetto_mancante(self):
        with mock.patch("nwn_voce.videoteca.aggiorna", return_value=0):
            self.apri(lambda *a, **k: self.fail("non doveva partire"), dati={"oggetto": "coppa"})
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "mancante"})])

    def test_oggetto_presente_passato_all_overlay_e_letto_sigillato(self):
        with open(os.path.join(palco.cartella_oggetti(), "coppa.vfo"), "wb") as fh:
            fh.write(sigillo.sigilla(b'{"m": []}', ".json"))

        def fa(s, righe, avvio):
            manda(s, {"ev": "chiusa", "dati": {"esito": "esc"}})
        popen, visto = finto_overlay(fa)
        self.apri(popen, dati={"oggetto": "coppa"})
        self.assertTrue(visto["avvio"]["oggetto"].endswith("coppa.vfo"))
        api = palco._Api(None, visto["avvio"])
        self.assertEqual(json.loads(api.oggetto()), {"m": []})

    def test_overlay_che_non_si_presenta(self):
        with mock.patch.object(palco, "ATTESA_OVERLAY", 0.3):
            self.apri(lambda *a, **k: FintoProc())
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "errore"})])

    def test_segreto_sbagliato_rifiutato(self):
        def popen(cmd, **_k):
            porta = int(cmd[-2])

            def gira():
                s = socket.create_connection(("127.0.0.1", porta))
                s.sendall(b'{"segreto": "sbagliato"}\n')
                time.sleep(0.3)
                s.close()
            threading.Thread(target=gira, daemon=True).start()
            return FintoProc()
        self.apri(popen)
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "errore"})])


if __name__ == "__main__":
    unittest.main(verbosity=2)
