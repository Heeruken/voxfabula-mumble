"""Il palco: scene interattive sopra Neverwinter Nights (es. esaminare un oggetto in 3D).

Uno script del server apre una scena a uno o piu' giocatori (vf_scena_inc.nss); il relay la
manda al Companion, che la mostra in un overlay TRASPARENTE incollato sull'area di gioco:
il gioco resta visibile sotto. Le decisioni (tiri, cosa si scopre) le prende sempre lo
script: qui si mostra e si riferisce quello che fa il giocatore.

Due meta':
  - ``Palco`` gira nell'app: riceve le scene dal relay, scarica i modelli che mancano,
    avvia l'overlay e fa da tramite (relay <-> overlay);
  - l'overlay e' un PROCESSO SEPARATO (``--palco <porta> <segreto>``), come il lettore
    video: se si pianta, la voce non ne risente. Parla con l'app su 127.0.0.1 (una riga
    JSON per messaggio), dopo essersi presentato col segreto ricevuto all'avvio.

Le pagine stanno DENTRO il Companion (web/palco): nessun codice arriva da internet. Dal
server arrivano solo dati e modelli (.vfo = JSON sigillato come i video, con impronta).
Ogni scena aperta o chiusa finisce nel registro attivita'.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

from . import APP_NAME, paths, settings

log = logging.getLogger("nwn_voce.palco")

TIPO_RE = re.compile(r"^[a-z0-9_]{1,24}$")
NOME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
TITOLO = APP_NAME + " - Palco"
ATTESA_OVERLAY = 15.0        # secondi perche' l'overlay si presenti
OGGETTO_MAX = 64 * 1024 * 1024


def cartella_oggetti() -> str:
    d = os.path.join(paths.data_dir(), "oggetti")
    os.makedirs(d, exist_ok=True)
    return d


def cartella_pagine() -> str:
    return os.path.join(os.path.dirname(paths.web_index()), "palco")


def tipo_esiste(tipo: str) -> bool:
    return bool(TIPO_RE.match(tipo or "")) and \
        os.path.isfile(os.path.join(cartella_pagine(), "scene", tipo + ".js"))


def trova_oggetto(nome: str) -> Optional[str]:
    if not NOME_RE.match(nome or ""):
        return None
    p = os.path.join(cartella_oggetti(), nome + ".vfo")
    return p if os.path.isfile(p) else None


def palco_attivo() -> bool:
    """Interruttore "Scene del server" (acceso di serie)."""
    return settings.load().get("palco_server", True) is not False


def scaffale():
    from . import videoteca
    return videoteca.Scaffale(videoteca.RADICE + "palco/", "oggetti", (".vfo",), cartella_oggetti,
                              "oggetto", max_file=OGGETTO_MAX)


# ====================================================================== nell'app


class Palco:
    """Una scena alla volta. Chiamato dal thread del relay: ogni metodo torna subito."""

    def __init__(self, registro) -> None:
        self.registro = registro
        self._lock = threading.Lock()
        self._sid: Optional[str] = None
        self._conn: Optional[socket.socket] = None
        self._coda: list = []                 # messaggi arrivati prima che l'overlay fosse pronto

    # -- dal relay
    def evento(self, tipo_msg: str, sid: str, tipo: str, dati: dict, client) -> None:
        if tipo_msg == "apri":
            threading.Thread(target=self._apri, args=(sid, tipo, dati, client),
                             name="palco", daemon=True).start()
        elif tipo_msg == "msg":
            self._manda(sid, {"msg": dati})
        elif tipo_msg == "chiudi":
            self._manda(sid, {"chiudi": 1})

    def _manda(self, sid: str, d: dict) -> None:
        with self._lock:
            if sid != self._sid:
                return
            if self._conn is None:
                self._coda.append(d)
                return
            conn = self._conn
        try:
            conn.sendall((json.dumps(d) + "\n").encode("utf-8"))
        except OSError:
            pass

    # -- una scena
    def _apri(self, sid: str, tipo: str, dati: dict, client) -> None:
        def chiusa(esito: str) -> None:
            client.send_scena_ev(sid, "chiusa", {"esito": esito})

        if not palco_attivo():
            self.registro.scrivi("scena_rifiutata", scena=tipo)
            return chiusa("disattivato")
        if not tipo_esiste(tipo):
            self.registro.scrivi("scena_sconosciuta", scena=tipo)
            return chiusa("sconosciuta")
        oggetto = str(dati.get("oggetto", "") or "")
        file_oggetto = ""
        if oggetto:
            file_oggetto = trova_oggetto(oggetto) or ""
            if not file_oggetto:
                # non ancora scaricato (pubblicato da poco): un tentativo adesso
                try:
                    from . import videoteca
                    videoteca.aggiorna(self.registro, sc=scaffale())
                except Exception:  # noqa: BLE001
                    log.exception("oggetti")
                file_oggetto = trova_oggetto(oggetto) or ""
            if not file_oggetto:
                self.registro.scrivi("oggetto_mancante", oggetto=oggetto)
                return chiusa("mancante")
        with self._lock:
            if self._sid is not None:
                busy = True
            else:
                busy = False
                self._sid, self._conn, self._coda = sid, None, []
        if busy:
            return chiusa("occupato")
        esito = "errore"
        self.registro.scrivi("scena_aperta", scena=tipo, oggetto=oggetto or "-")
        try:
            esito = self._overlay(sid, tipo, dati, file_oggetto, client)
        except Exception:  # noqa: BLE001 -- mai far cadere la voce
            log.exception("palco")
        finally:
            with self._lock:
                self._sid, self._conn, self._coda = None, None, []
        self.registro.scrivi("scena_chiusa", scena=tipo, esito=esito)
        chiusa(esito)

    def _overlay(self, sid, tipo, dati, file_oggetto, client) -> str:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(ATTESA_OVERLAY)
        segreto = secrets.token_hex(16)
        porta = srv.getsockname()[1]
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--palco", str(porta), segreto]
        else:
            cmd = [sys.executable, "-m", "nwn_voce", "--palco", str(porta), segreto]
        proc = subprocess.Popen(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            try:
                conn, _ = srv.accept()
            except OSError:
                log.warning("l'overlay non si e' presentato")
                return "errore"
            conn.settimeout(None)
            righe = conn.makefile("rb")
            if json.loads(righe.readline(4096) or b"{}").get("segreto") != segreto:
                log.warning("overlay sconosciuto: rifiutato")
                conn.close()
                return "errore"
            conn.sendall((json.dumps({"sid": sid, "tipo": tipo, "dati": dati,
                                      "oggetto": file_oggetto}) + "\n").encode("utf-8"))
            with self._lock:
                self._conn, coda, self._coda = conn, self._coda, []
            for d in coda:
                conn.sendall((json.dumps(d) + "\n").encode("utf-8"))
            esito = "errore"
            for riga in righe:
                try:
                    m = json.loads(riga)
                except ValueError:
                    continue
                ev, d = str(m.get("ev", "")), m.get("dati", {})
                if ev == "chiusa":
                    esito = str((d or {}).get("esito", "chiusa"))[:24]
                    break
                if TIPO_RE.match(ev) and isinstance(d, dict):
                    client.send_scena_ev(sid, ev, d)
            conn.close()
            return esito
        finally:
            srv.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


# ====================================================================== l'overlay


class _Api:
    """Chiamate dalla pagina. Solo attributi privati (vedi main.py)."""

    def __init__(self, conn: socket.socket, avvio: dict) -> None:
        self._conn = conn
        self._avvio = avvio
        self._lock = threading.Lock()
        self._finestra = None
        self._chiuso = threading.Event()

    def inizio(self) -> dict:
        return {"sid": self._avvio.get("sid"), "tipo": self._avvio.get("tipo"),
                "dati": self._avvio.get("dati") or {}}

    def oggetto(self) -> str:
        """Il modello della scena (JSON in chiaro, solo in memoria), "" se non c'e'."""
        f = self._avvio.get("oggetto") or ""
        if not f:
            return ""
        from . import sigillo
        try:
            with open(f, "rb") as fh:
                dati, _est = sigillo.apri(fh.read())
            return dati.decode("utf-8")
        except (OSError, ValueError) as exc:
            log.warning("oggetto illeggibile: %s", exc)
            return ""

    def invia(self, ev, dati=None) -> bool:
        if not isinstance(ev, str) or not TIPO_RE.match(ev) or ev == "chiusa":
            return False
        return self._scrivi({"ev": ev, "dati": dati if isinstance(dati, dict) else {}})

    def chiudi(self, esito="chiusa") -> None:
        if self._chiuso.is_set():
            return
        self._chiuso.set()
        self._scrivi({"ev": "chiusa", "dati": {"esito": str(esito)[:24]}})
        if self._finestra is not None:
            try:
                self._finestra.destroy()
            except Exception:  # noqa: BLE001
                pass

    def _scrivi(self, d: dict) -> bool:
        try:
            with self._lock:
                self._conn.sendall((json.dumps(d) + "\n").encode("utf-8"))
            return True
        except OSError:
            return False


def _overlay(porta: int, segreto: str) -> int:
    from . import cinema as C

    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass

    conn = socket.create_connection(("127.0.0.1", porta), timeout=10)
    conn.sendall((json.dumps({"segreto": segreto}) + "\n").encode("utf-8"))
    righe = conn.makefile("rb")
    avvio = json.loads(righe.readline(1 << 20) or b"{}")
    conn.settimeout(None)
    if not TIPO_RE.match(str(avvio.get("tipo", ""))):
        return 1

    nwn = C.finestra_nwn()
    esclusivo = bool(nwn) and C.nwn_esclusivo()
    rett = None if esclusivo else C.area_di_gioco(nwn)
    if esclusivo:
        C._u32.ShowWindow(nwn, C.SW_MINIMIZE)
    log.info("scena %s (%s), NWN area=%s esclusivo=%s", avvio.get("tipo"), avvio.get("sid"), rett, esclusivo)

    import webview
    api = _Api(conn, avvio)
    pagina = os.path.join(cartella_pagine(), "index.html")
    api._finestra = webview.create_window(TITOLO, pagina, js_api=api, fullscreen=rett is None,
                                          frameless=True, easy_drag=False, on_top=True, hidden=True,
                                          transparent=True, focus=True)

    def ascolta():
        # dall'app: messaggi dello script per la pagina, o "chiudi"
        try:
            for riga in righe:
                m = json.loads(riga)
                if m.get("chiudi"):
                    api.chiudi("server")
                    return
                if isinstance(m.get("msg"), dict):
                    api._finestra.evaluate_js("window.palcoRicevi && window.palcoRicevi(%s)"
                                              % json.dumps(m["msg"]))
        except (OSError, ValueError):
            pass
        api.chiudi("app")                 # l'app se n'e' andata: niente scene orfane

    def guardiano():
        mia = None
        for _ in range(50):
            mia = C._u32.FindWindowW(None, TITOLO)
            if mia:
                break
            time.sleep(0.05)
        api._finestra.show()
        if mia:
            C._adatta(mia, rett)
        C.porta_davanti(mia)
        threading.Thread(target=ascolta, daemon=True).start()
        while not api._chiuso.wait(0.5):
            if mia and not esclusivo:
                C._incolla(mia, C.area_di_gioco(nwn) or rett)

    webview.start(guardiano, private_mode=True, http_server=True)
    if not api._chiuso.is_set():
        api.chiudi("chiusa")
    if nwn:
        C.porta_davanti(nwn)
    return 0


def main_palco(argv: list[str]) -> int:
    """Ingresso del processo overlay: ``--palco <porta> <segreto>``."""
    logging.basicConfig(
        filename=os.path.join(paths.data_dir(), "palco.log"), filemode="w",
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    try:
        return _overlay(int(argv[0]), argv[1])
    except Exception:  # noqa: BLE001
        log.exception("errore dell'overlay")
        return 1
