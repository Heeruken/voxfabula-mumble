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
    poi torna "pronto" (come la pagina quando si e' ripulita)."""

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

    def test_messaggi_arrivati_prima_dell_overlay_aspettano(self):
        # lo script apre la scena e manda subito un messaggio: il relay li consegna insieme,
        # quando l'overlay non ha ancora la scena. Il messaggio aspetta e arriva DOPO "apri".
        arrivati = []

        def fa(s, apri, righe):
            arrivati.append(json.loads(righe.readline()))
            arrivati.append(json.loads(righe.readline()))
            manda(s, {"ev": "chiusa", "dati": {"esito": "esc"}})
        ov = FintoOverlay(fa)
        with mock.patch.object(palco.subprocess, "Popen", side_effect=ov.popen):
            self.palco.evento("apri", "ab12", "esamina", {}, self.client)
            self.palco.evento("msg", "ab12", "", {"tipo": "testo", "testo": "uno"}, self.client)
            self.palco.evento("msg", "ab12", "", {"tipo": "testo", "testo": "due"}, self.client)
            fine = time.time() + 5
            while not self.client.ev and time.time() < fine:
                time.sleep(0.02)
        self.assertEqual([m["msg"]["testo"] for m in arrivati], ["uno", "due"])
        self.assertEqual(self.client.ev, [("ab12", "chiusa", {"esito": "esc"})])
        self.assertEqual(self.palco._coda, {})

    def test_relay_perso_chiude_la_scena(self):
        # il collegamento cade a meta' scena: niente scena "zombie" sullo schermo
        arrivati = []

        def fa(s, apri, righe):
            manda(s, {"ev": "pronta", "dati": {}})
            m = json.loads(righe.readline())
            arrivati.append(m)
            manda(s, {"ev": "chiusa", "dati": {"esito": m["chiudi"]}})
        ov = FintoOverlay(fa)
        t = threading.Thread(target=self.apri, args=(ov,))
        t.start()
        fine = time.time() + 5
        while not self.client.ev and time.time() < fine:
            time.sleep(0.02)
        self.palco.evento("perso", "", "", {}, FintoClient())      # un ALTRO client: non e' la sua
        self.palco.evento("perso", "", "", {}, self.client)
        t.join(5)
        self.assertEqual(arrivati, [{"chiudi": "disconnesso"}])
        self.assertEqual(self.client.ev[-1], ("ab12", "chiusa", {"esito": "disconnesso"}))

    def test_scollega_spegne_l_overlay_ma_non_quello_nuovo(self):
        ov = FintoOverlay(lambda *a: None)
        with mock.patch.object(palco.subprocess, "Popen", side_effect=ov.popen):
            self.assertIsNotNone(self.palco._overlay_pronto())
            vecchio = self.palco._ov
            self.palco.scollega()                       # Disconnetti...
            nuovo = self.palco._overlay_pronto()        # ...e subito Connetti
            fine = time.time() + 5
            while vecchio["vivo"] and time.time() < fine:
                time.sleep(0.02)
        self.assertFalse(vecchio["vivo"])
        self.assertIsNotNone(nuovo)
        self.assertIs(self.palco._ov, nuovo)
        self.assertTrue(nuovo["vivo"])
        self.assertEqual(ov.avvii, 2)


class CacheTest(unittest.TestCase):
    def test_porta_fissa_se_libera_altrimenti_a_caso(self):
        self.assertEqual(palco.porta_pagine(), palco.PORTA_PAGINE)
        occupata = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupata.bind(("localhost", palco.PORTA_PAGINE))
        try:
            self.assertIsNone(palco.porta_pagine())
        finally:
            occupata.close()

    def test_cartella_cache_nei_dati_dell_app(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(paths, "data_dir", return_value=d):
            self.assertEqual(palco.cartella_cache(), os.path.join(d, "palco_cache"))
            self.assertTrue(os.path.isdir(os.path.join(d, "palco_cache")))


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
        self.assertEqual(api.chiudi("esc"), "ab12")    # la pagina l'aveva: va ripulita
        self.assertIsNone(api.chiudi("esc"))           # la seconda non fa niente
        time.sleep(0.1)
        self.assertEqual(chiamate, [1])
        a.close()
        b.close()

    def test_chiusa_prima_che_la_pagina_la_prenda(self):
        # il server chiude la scena prima che la pagina l'abbia presa: la pagina resta in
        # attesa (non si ferma per sempre) e l'app sa che e' di nuovo pronta
        a, b = socket.socketpair()
        letto = b.makefile("rb")
        api = palco._Api(a)
        api._dopo_chiusa = lambda: None
        risultato = []
        t = threading.Thread(target=lambda: risultato.append(api.inizio()))
        api._scena = {"sid": "ab12", "tipo": "esamina"}       # arrivata ma non ancora presa
        self.assertIsNone(api.chiudi("server"))               # la pagina non l'aveva: niente da ripulire
        t.start()
        righe = [json.loads(letto.readline()) for _ in range(3)]
        self.assertIn({"ev": "chiusa", "dati": {"esito": "server"}}, righe)
        self.assertEqual(righe.count({"pronto": 1}), 2)
        time.sleep(0.7)
        self.assertEqual(risultato, [])                        # sempre in attesa
        api.nuova({"sid": "cd34", "tipo": "esamina", "dati": {}})
        t.join(3)
        self.assertEqual(risultato[0]["sid"], "cd34")
        api._finito.set()
        a.close()
        b.close()


class PagineTest(unittest.TestCase):
    """Le pagine del palco: ogni import deve trovare il suo file (three.js compreso)."""

    def test_import_risolti(self):
        import re
        base = palco.cartella_pagine()
        for radice, _d, files in os.walk(base):
            for f in files:
                if not f.endswith(".js") or "vendor" in radice:
                    continue
                testo = open(os.path.join(radice, f), encoding="utf-8").read()
                for imp in re.findall(r"""(?:from|import)\s*\(?\s*['"](\.{1,2}/[^'"]+)['"]""", testo):
                    if imp.endswith("/"):              # import('./scene/' + tipo + '.js')
                        continue
                    dest = os.path.normpath(os.path.join(radice, imp))
                    self.assertTrue(os.path.isfile(dest), f"{f}: manca {imp}")

    def test_three_e_licenza(self):
        v = os.path.join(palco.cartella_pagine(), "vendor")
        self.assertTrue(os.path.isfile(os.path.join(v, "three.module.js")))
        self.assertTrue(os.path.isfile(os.path.join(v, "LICENSE-three.txt")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
