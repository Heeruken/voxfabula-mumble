"""Test della regia e della videoteca, senza rete e senza aprire finestre."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from nwn_voce import net_protocol as proto, paths, regia, settings, sigillo, videoteca  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = mock.patch.object(paths, "data_dir", lambda: self.tmp.name)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.eventi = []
        self.registro = regia.Registro(lambda k, **d: self.eventi.append((k, d)))


class RegiaTest(_Base):
    def esito(self, nome):
        risposte = []
        r = regia.Regia(self.registro)
        out = r._mostra(nome, "r1")
        risposte.append(out)
        return out

    def test_nome_strano_e_mancante(self):
        for nome in ("../../windows/system32/x", "C:\\x", "Video", "a b", ""):
            self.assertIsNone(regia.trova_video(nome))
        self.assertEqual(self.esito("non_esiste"), "mancante")

    def test_interruttore_spento(self):
        settings.update(video_server=False)
        self.assertEqual(self.esito("qualsiasi"), "disattivato")
        self.assertTrue(any(d.get("voce") == "video_rifiutato" for _, d in self.eventi))

    def test_occupato(self):
        open(os.path.join(regia.cartella_video(), "a.webm"), "wb").close()
        r = regia.Regia(self.registro)
        r._in_corso = "altro"
        self.assertEqual(r._mostra("a", "r2"), "occupato")

    def test_esito_dal_lettore(self):
        open(os.path.join(regia.cartella_video(), "a.webm"), "wb").close()
        r = regia.Regia(self.registro)
        for codice, atteso in ((0, "visto"), (3, "saltato"), (1, "errore"), (7, "errore")):
            with mock.patch.object(regia.subprocess, "run",
                                   return_value=mock.Mock(returncode=codice)):
                self.assertEqual(r._mostra("a", "r3"), atteso)
        self.assertIsNone(r._in_corso)

    def test_registro_su_file(self):
        self.registro.scrivi("video_inizio", video="x")
        again = regia.Registro(lambda *a, **k: None)
        self.assertTrue(again.righe()[-1].endswith("video_inizio  video=x"))

    def test_esito_sconosciuto_diventa_errore(self):
        self.assertIn(b'"errore"', proto.encode_video_esito("r", "boh"))


class VideotecaTest(_Base):
    def voce(self, nome, dati):
        return {"nome": nome, "file": nome + ".webm",
                "sha256": hashlib.sha256(dati).hexdigest(), "size": len(dati)}

    def test_solo_https_e_nostro_dominio(self):
        self.assertTrue(videoteca._sicuro("https://nwsync.voxfabula.it/cinema/x.webm"))
        for url in ("http://nwsync.voxfabula.it/x", "https://evil.example/x",
                    "https://nwsync.voxfabula.it.evil.example/x", "file:///C:/x"):
            self.assertFalse(videoteca._sicuro(url), url)

    def test_voci_malformate_scartate(self):
        buona = self.voce("ingresso", b"abc")
        cattive = [
            dict(buona, nome="../x"), dict(buona, file="altro.webm"),
            dict(buona, file="ingresso.exe"), dict(buona, sha256="zz"),
            dict(buona, size=0), dict(buona, size=10 ** 10), {"nome": "x"},
        ]
        self.assertEqual(videoteca._voci_valide({"video": [buona] + cattive}), [buona])

    def test_file_gia_presente_non_si_riscarica(self):
        dati = b"video finto"
        with open(os.path.join(regia.cartella_video(), "ingresso.webm"), "wb") as fh:
            fh.write(dati)
        with mock.patch.object(videoteca, "_scarica", side_effect=AssertionError("rete!")):
            self.assertEqual(videoteca.aggiorna(self.registro, {"video": [self.voce("ingresso", dati)]}), 0)

    def test_catalogo_rotto_non_cancella(self):
        f = os.path.join(regia.cartella_video(), "vecchio.webm")
        open(f, "wb").close()
        videoteca._scrivi_locale(regia.cartella_video(), {"vecchio.webm": "0" * 64})
        videoteca.aggiorna(self.registro, {"niente": 1})
        self.assertTrue(os.path.exists(f))

    def test_tolti_solo_quelli_nostri(self):
        cart = regia.cartella_video()
        for n in ("nostro.webm", "suo.webm"):
            open(os.path.join(cart, n), "wb").close()
        videoteca._scrivi_locale(cart, {"nostro.webm": "0" * 64})
        videoteca.aggiorna(self.registro, {"video": []})
        self.assertFalse(os.path.exists(os.path.join(cart, "nostro.webm")))
        self.assertTrue(os.path.exists(os.path.join(cart, "suo.webm")))


class SigilloTest(_Base):
    def test_andata_e_ritorno(self):
        dati = os.urandom(100000)
        blob = sigillo.sigilla(dati, ".webm")
        self.assertNotIn(dati[:64], blob)                  # non si legge in chiaro
        self.assertEqual(sigillo.apri(blob), (dati, ".webm"))
        self.assertNotEqual(blob, sigillo.sigilla(dati, ".webm"))   # nonce diverso ogni volta

    def test_file_normale_rifiutato(self):
        with self.assertRaises(ValueError):
            sigillo.apri(b"\x1aE\xdf\xa3 webm normale")

    def test_regia_preferisce_il_sigillato(self):
        cart = regia.cartella_video()
        for n in ("x.webm", "x.vfv"):
            open(os.path.join(cart, n), "wb").close()
        self.assertTrue(regia.trova_video("x").endswith(".vfv"))

    def test_lettore_apre_e_ripulisce(self):
        from nwn_voce import cinema
        cart = regia.cartella_video()
        dati = b"finto video " * 1000
        with open(os.path.join(cart, "f.vfv"), "wb") as fh:
            fh.write(sigillo.sigilla(dati, ".webm"))
        open(os.path.join(cart, "_sipario_vecchio.webm"), "wb").close()   # avanzo di un crash
        visti = []

        def finto(path):
            visti.append((os.path.basename(path), open(path, "rb").read()))
            return cinema.FINE
        with mock.patch.object(cinema, "_riproduci_file", finto):
            self.assertEqual(cinema.riproduci(os.path.join(cart, "f.vfv")), cinema.FINE)
        self.assertTrue(visti[0][0].startswith("_sipario_") and visti[0][0].endswith(".webm"))
        self.assertEqual(visti[0][1], dati)
        self.assertEqual([f for f in os.listdir(cart) if f.startswith("_sipario_")], [])

    def test_sigillato_rovinato_e_errore(self):
        from nwn_voce import cinema
        p = os.path.join(regia.cartella_video(), "r.vfv")
        open(p, "wb").write(b"VFV1\x05.we")
        self.assertEqual(cinema.riproduci(p), cinema.ERRORE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
