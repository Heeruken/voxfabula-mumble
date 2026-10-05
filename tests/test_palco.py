"""Test del palco (scene interattive) lato app: niente finestre, l'overlay e' finto
(un thread che si collega alla porta come farebbe il processo --palco, e resta acceso)."""

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


def manda(s, d):
    s.sendall((json.dumps(d) + "\n").encode())


class FintoOverlay:
    """Fa l'overlay: si presenta, dice "pronto", e per ogni scena chiama ``scena(sock, apri, righe)``;
    poi torna "pronto" (come dopo il ricaricamento della pagina)."""

    def __init__(self, scena, segreto_giusto=True):
        self.scena, self.segreto_giusto = scena, segreto_giusto
        self.avvii = 0
        self.aperte = []
        self.sock = None

    def popen(self, cmd, **_k):
        self.avvii += 1
        porta, segreto = int(cmd[-2]), cmd[-1]

        def gira():
            s = socket.create_connection(("127.0.0.1", porta))
            self.sock = s
            s.sendall((json.dumps({"segreto": segreto if self.segreto_giusto else "no"}) + "\n").encode())
            righe = s.makefile("rb")
            manda(s, {"pronto": 1})
            try:
                for riga in righe:
                    m = json.loads(riga)
                    if m.get("esci"):
                        break
                    if "apri" in m:
                        self.aperte.append(m["apri"])
                        self.scena(s, m["apri"], righe)
                        manda(s, {"pronto": 1})
            except (OSError, ValueError):
                pass
            s.close()
        threading.Thread(target=gira, daemon=True).start()
        return FintoProc()


class PalcoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(paths, "data_dir", return_value=self.tmp.name)
        self.p.start()
        self.reg = Registro()
        self.palco = palco.Palco(self.reg)
        self.client = FintoClient()

    def tearDown(self):
        self.palco.chiudi_tutto()
        self.p.stop()
        self.tmp.cleanup()

    def apri(self, ov, tipo="esamina", dati=None, sid="ab12"):
        with mock.patch.object(palco.subprocess, "Popen", side_effect=ov.popen):
            self.palco._apri(sid, tipo, dati if dati is not None else {}, self.client)

    def test_scena_eventi_ed_esito(self):
        def fa(s, apri, righe):
            manda(s, {"ev": "pronta", "dati": {}})
            manda(s, {"ev": "azione", "dati": {"id": "indaga"}})
            manda(s, {"ev": "chiusa", "dati": {"esito": "esc"}})
        ov = FintoOverlay(fa)
        self.apri(ov, dati={"titolo": "Coppa"})
        self.assertEqual(ov.aperte[0]["tipo"], "esamina")
        self.assertEqual(ov.aperte[0]["dati"], {"titolo": "Coppa"})
        self.assertEqual(self.client.ev, [("ab12", "pronta", {}), ("ab12", "azione", {"id": "indaga"}),
                                          ("ab12", "chiusa", {"esito": "esc"})])
        self.assertEqual([r[0] for r in self.reg.righe], ["scena_aperta", "scena_chiusa"])

    def test_overlay_preparato_e_riusato(self):
        # al Connetti parte e resta pronto; due scene di fila usano lo STESSO processo
        def fa(s, apri, righe):
            manda(s, {"ev": "chiusa", "dati": {"esito": "esc"}})
        ov = FintoOverlay(fa)
        with mock.patch.object(palco.subprocess, "Popen", side_effect=ov.popen):
            self.palco.prepara()
            fine = time.time() + 5
            while (self.palco._ov is None or not self.palco._ov["pronto"].is_set()) and time.time() < fine:
                time.sleep(0.02)
            self.assertTrue(self.palco._ov["pronto"].is_set())
            self.palco._apri("aa01", "esamina", {}, self.client)
            self.palco._apri("aa02", "esamina", {}, self.client)
        self.assertEqual(ov.avvii, 1)
        self.assertEqual([a["sid"] for a in ov.aperte], ["aa01", "aa02"])
        self.assertEqual([e[:2] for e in self.client.ev], [("aa01", "chiusa"), ("aa02", "chiusa")])

    def test_messaggi_del_server_arrivano_all_overlay(self):
        arrivati = []

        def fa(s, apri, righe):
            manda(s, {"ev": "pronta", "dati": {}})
            arrivati.append(json.loads(righe.readline()))
            manda(s, {"ev": "chiusa", "dati": {"esito": "chiusa"}})
        ov = FintoOverlay(fa)
        t = threading.Thread(target=self.apri, args=(ov,))
        t.start()
        fine = time.time() + 5
        while not self.client.ev and time.time() < fine:
            time.sleep(0.02)
        self.palco.evento("msg", "zz99", "", {"tipo": "altra scena"}, self.client)   # non e' la sua
        self.palco.evento("msg", "ab12", "", {"tipo": "tiro", "d20": 17}, self.client)
        t.join(5)
        self.assertEqual(arrivati, [{"msg": {"tipo": "tiro", "d20": 17}}])

    def test_overlay_che_muore_durante_la_scena(self):
        def fa(s, apri, righe):
            s.shutdown(socket.SHUT_RDWR)       # come un processo che muore: la connessione cade davvero
        self.apri(FintoOverlay(fa))
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "errore"})])
        self.assertIsNone(self.palco._ov)

    def test_tipo_sconosciuto(self):
        self.apri(FintoOverlay(lambda *a: self.fail("non doveva partire")), tipo="nonesiste")
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "sconosciuta"})])

    def test_interruttore_spento(self):
        with mock.patch.object(palco, "palco_attivo", return_value=False):
            self.apri(FintoOverlay(lambda *a: self.fail("non doveva partire")))
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "disattivato"})])

    def test_oggetto_mancante(self):
        with mock.patch("nwn_voce.videoteca.aggiorna", return_value=0):
            self.apri(FintoOverlay(lambda *a: self.fail("non doveva partire")), dati={"oggetto": "coppa"})
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "mancante"})])

    def test_oggetto_presente_passato_all_overlay_e_letto_sigillato(self):
        with open(os.path.join(palco.cartella_oggetti(), "coppa.vfo"), "wb") as fh:
            fh.write(sigillo.sigilla(b'{"m": []}', ".json"))

        def fa(s, apri, righe):
            manda(s, {"ev": "chiusa", "dati": {"esito": "esc"}})
        ov = FintoOverlay(fa)
        self.apri(ov, dati={"oggetto": "coppa"})
        self.assertTrue(ov.aperte[0]["oggetto"].endswith("coppa.vfo"))
        api = palco._Api(None)
        api._scena = ov.aperte[0]
        self.assertEqual(json.loads(api.oggetto()), {"m": []})

    def test_overlay_che_non_si_presenta(self):
        with mock.patch.object(palco, "ATTESA_OVERLAY", 0.3), \
                mock.patch.object(palco.subprocess, "Popen", return_value=FintoProc()):
            self.palco._apri("ab12", "esamina", {}, self.client)
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "errore"})])

    def test_segreto_sbagliato_rifiutato(self):
        self.apri(FintoOverlay(lambda *a: None, segreto_giusto=False))
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "errore"})])


class ApiOverlayTest(unittest.TestCase):
    """La meta' nell'overlay: inizio() aspetta la scena, chiudi() la chiude una volta sola."""

    def test_inizio_aspetta_la_scena(self):
        a, b = socket.socketpair()
        api = palco._Api(a)
        risultato = []
        t = threading.Thread(target=lambda: risultato.append(api.inizio()))
        t.start()
        self.assertEqual(json.loads(b.makefile("rb").readline()), {"pronto": 1})
        api._scena = {"sid": "ab12", "tipo": "esamina", "dati": {"x": 1}}
        api._arriva.set()
        t.join(3)
        self.assertEqual(risultato, [{"sid": "ab12", "tipo": "esamina", "dati": {"x": 1}}])
        chiamate = []
        api._dopo_chiusa = lambda: chiamate.append(1)
        api.chiudi("esc")
        api.chiudi("esc")                        # la seconda non fa niente
        time.sleep(0.1)
        self.assertEqual(chiamate, [1])
        a.close()
        b.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
