"""Il palco: scene interattive sopra Neverwinter Nights (es. esaminare un oggetto in 3D).

Uno script del server apre una scena a uno o piu' giocatori (vf_scena_inc.nss); il relay la
manda al Companion, che la mostra in un overlay TRASPARENTE incollato sull'area di gioco:
il gioco resta visibile sotto (o a nero, se lo script lo oscura come una cutscene). Le
decisioni (tiri, cosa si scopre) le prende sempre lo script: qui si mostra e si riferisce.

Due meta':
  - ``Palco`` gira nell'app: riceve le scene dal relay, scarica i modelli che mancano e fa da
    tramite (relay <-> overlay);
  - l'overlay e' un PROCESSO SEPARATO (``--palco <porta> <segreto>``), come il lettore video:
    se si pianta, la voce non ne risente. Parla con l'app su 127.0.0.1 (una riga JSON per
    messaggio), dopo essersi presentato col segreto ricevuto all'avvio.

L'overlay si avvia GIA' al Connetti e resta pronto, nascosto, con la pagina caricata: quando
arriva una scena compare subito (avviare processo e browser costava qualche secondo). Finita
la scena si nasconde e la pagina si ripulisce da sola, pronta per la prossima. Si chiude al
Disconnetti e quando si spengono le scene.

Nascosto DAVVERO: pywebview 6.x, con una finestra trasparente, la mostra (Show + Activate) a
ogni navigazione della pagina anche se creata ``hidden`` (edgechromium.on_navigation_start).
Per questo la finestra nasce FUORI SCHERMO, la pagina non si ricarica mai tra una scena e
l'altra, e un guardiano la rinasconde (ridando il gioco in primo piano) se compare da sola.

Protocollo:
    app -> overlay:  {"apri": {sid, tipo, dati, oggetto}}  {"msg": {..}}  {"chiudi": 1 | "<esito>"}
                     {"esci": 1}
    overlay -> app:  {"segreto": ..} (il primo)  {"pronto": 1} (pagina in attesa di una scena)
                     {"ev": .., "dati": {..}}  ("chiusa" = scena finita)

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
ATTESA_OVERLAY = 20.0        # secondi perche' l'overlay sia pronto (primo avvio: processo + browser)
SCENA_MAX = 3 * 3600.0       # tetto di una scena aperta
OGGETTO_MAX = 64 * 1024 * 1024
FUORI = -32000               # dove sta la finestra dell'overlay quando non c'e' una scena
# Le pagine si servono sempre dalla stessa porta: stessa origine a ogni avvio, cosi' la cache di
# WebView2 (JavaScript gia' compilato, three.js compreso) vale anche la volta dopo.
PORTA_PAGINE = 47913


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


def cartella_cache() -> str:
    """Il profilo di WebView2 dell'overlay: cache del JavaScript gia' compilato e degli shader.
    Solo roba del browser (nessun dato del giocatore): si puo' cancellare quando si vuole."""
    d = os.path.join(paths.data_dir(), "palco_cache")
    os.makedirs(d, exist_ok=True)
    return d


def porta_pagine() -> Optional[int]:
    """PORTA_PAGINE se libera; se no None (porta a caso: si perde solo la cache del JavaScript)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("localhost", PORTA_PAGINE))
        return PORTA_PAGINE
    except OSError:
        return None
    finally:
        s.close()


def palco_attivo() -> bool:
    """Interruttore "Scene del server" (acceso di serie)."""
    return settings.load().get("palco_server", True) is not False


def scaffale():
    from . import videoteca
    return videoteca.Scaffale(videoteca.RADICE + "palco/", "oggetti", (".vfo",), cartella_oggetti,
                              "oggetto", max_file=OGGETTO_MAX)


def _riga(d: dict) -> bytes:
    return (json.dumps(d) + "\n").encode("utf-8")


# ====================================================================== nell'app


class Palco:
    """Una scena alla volta. Chiamato dal thread del relay: ogni metodo torna subito."""

    def __init__(self, registro) -> None:
        self.registro = registro
        self._lock = threading.Lock()          # la scena in corso (e le code)
        self._ov_lock = threading.Lock()       # l'overlay (avvio)
        self._ov: Optional[dict] = None        # {proc, conn, pronto, vivo, invio}
        self._scena: Optional[dict] = None     # {sid, client, fine, esito, ov}
        # scene annunciate dal relay ma non ancora sull'overlay: i messaggi che lo script manda
        # subito dopo l'apertura aspettano qui, in ordine {sid: {"client", "righe"}}
        self._coda: dict = {}
        self._gen_ov = 0                       # +1 a ogni scollega: gli overlay di prima si spengono

    # -- dal motore
    def prepara(self) -> None:
        """Al Connetti: l'overlay parte nascosto e resta pronto (la prima scena compare subito)."""
        if palco_attivo():
            threading.Thread(target=self._overlay_pronto, name="palco-prepara", daemon=True).start()

    def scollega(self, client=None, esito: str = "disconnesso") -> None:
        """Disconnetti (o scene spente): chiude la scena aperta ("disconnesso") e spegne l'overlay
        senza aspettare. Solo quello avviato FINO A ORA: se intanto si ricollega, il nuovo resta."""
        self._perso(client, esito)
        self._gen_ov += 1
        threading.Thread(target=self._spegni_prima_di, args=(self._gen_ov,),
                         name="palco-chiudi", daemon=True).start()

    def _spegni_prima_di(self, gen: int) -> None:
        with self._ov_lock:                    # aspetta anche un overlay che sta partendo
            ov = self._ov
            if ov is None or ov.get("gen", 0) >= gen:
                return
            self._ov = None
        self._spegni(ov)

    def chiudi_tutto(self) -> None:
        """L'app si chiude: via l'overlay."""
        with self._ov_lock:
            ov, self._ov = self._ov, None
        if ov is not None:
            self._spegni(ov)

    def _spegni(self, ov: dict) -> None:
        self._scrivi(ov, {"esci": 1})
        try:
            ov["proc"].wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                ov["proc"].kill()
            except Exception:  # noqa: BLE001
                pass

    # -- dal relay
    def evento(self, tipo_msg: str, sid: str, tipo: str, dati: dict, client) -> None:
        if tipo_msg == "apri":
            with self._lock:
                self._coda[sid] = {"client": client, "righe": []}
            threading.Thread(target=self._apri, args=(sid, tipo, dati, client),
                             name="palco", daemon=True).start()
        elif tipo_msg == "msg":
            self._alla_scena(sid, {"msg": dati})
        elif tipo_msg == "chiudi":
            self._alla_scena(sid, {"chiudi": 1})
        elif tipo_msg == "perso":
            self._perso(client)               # relay caduto: il server ha gia' chiuso le scene

    def _perso(self, client=None, esito: str = "disconnesso") -> None:
        """Il collegamento col relay non c'e' piu' (o le scene sono state spente): la scena
        aperta, o in arrivo, di quel client si chiude (il relay l'ha gia' chiusa per lo script)."""
        with self._lock:
            for c in self._coda.values():
                if client is None or c["client"] is client:
                    c["righe"].append({"chiudi": esito})
            sc = self._scena
            ov = sc.get("ov") if sc is not None and (client is None or sc["client"] is client) else None
        if ov is not None:
            self._scrivi(ov, {"chiudi": esito})

    def _alla_scena(self, sid: str, d: dict) -> None:
        with self._lock:
            attesa = self._coda.get(sid)
            if attesa is not None:            # la scena sta ancora arrivando sull'overlay
                attesa["righe"].append(d)
                return
            sc = self._scena
            ov = sc.get("ov") if sc is not None and sc["sid"] == sid else None
        if ov is not None:
            self._scrivi(ov, d)

    @staticmethod
    def _scrivi(ov: dict, d: dict) -> bool:
        try:
            with ov["invio"]:
                ov["conn"].sendall(_riga(d))
            return True
        except OSError:
            return False

    # -- l'overlay (processo a parte, tenuto pronto)
    def _overlay_pronto(self, attesa: float = ATTESA_OVERLAY) -> Optional[dict]:
        with self._ov_lock:
            ov = self._ov
            if ov is None or not ov["vivo"]:
                gen = self._gen_ov
                ov = self._avvia_overlay()
                if ov is not None:
                    ov["gen"] = gen
                self._ov = ov
        if ov is None or not ov["pronto"].wait(attesa):
            return None
        return ov if ov["vivo"] else None

    def _avvia_overlay(self) -> Optional[dict]:
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
        try:
            proc = subprocess.Popen(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            log.exception("overlay del palco")
            srv.close()
            return None
        try:
            conn, _ = srv.accept()
        except OSError:
            log.warning("l'overlay non si e' presentato")
            proc.kill()
            return None
        finally:
            srv.close()
        conn.settimeout(None)
        righe = conn.makefile("rb")
        try:
            ok = json.loads(righe.readline(4096) or b"{}").get("segreto") == segreto
        except ValueError:
            ok = False
        if not ok:
            log.warning("overlay sconosciuto: rifiutato")
            conn.close()
            proc.kill()
            return None
        ov = {"proc": proc, "conn": conn, "pronto": threading.Event(), "vivo": True, "invio": threading.Lock()}
        threading.Thread(target=self._leggi, args=(ov, righe), name="palco-overlay", daemon=True).start()
        return ov

    def _leggi(self, ov: dict, righe) -> None:
        try:
            for riga in righe:
                try:
                    m = json.loads(riga)
                except ValueError:
                    continue
                if m.get("pronto"):
                    ov["pronto"].set()
                    continue
                ev, d = str(m.get("ev", "")), m.get("dati", {})
                with self._lock:
                    sc = self._scena
                if sc is None or sc.get("ov") is not ov:
                    continue                              # nessuna scena (o di un overlay vecchio)
                if ev == "chiusa":
                    ov["pronto"].clear()                  # la pagina si ripulisce: un attimo e torna pronta
                    sc["esito"] = str((d or {}).get("esito", "chiusa"))[:24]
                    sc["fine"].set()
                elif TIPO_RE.match(ev) and isinstance(d, dict):
                    sc["client"].send_scena_ev(sc["sid"], ev, d)
        except (OSError, ValueError):
            pass
        ov["vivo"] = False
        ov["pronto"].clear()
        with self._ov_lock:
            if self._ov is ov:
                self._ov = None
        with self._lock:
            sc = self._scena
        if sc is not None and sc.get("ov") is ov and not sc["fine"].is_set():
            sc["esito"] = "errore"
            sc["fine"].set()

    # -- una scena
    def _apri(self, sid: str, tipo: str, dati: dict, client) -> None:
        def chiusa(esito: str) -> None:
            with self._lock:
                self._coda.pop(sid, None)
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
        sc = {"sid": sid, "client": client, "fine": threading.Event(), "esito": "errore", "ov": None}
        with self._lock:
            if self._scena is not None:
                sc = None
            else:
                self._scena = sc
        if sc is None:
            return chiusa("occupato")
        self.registro.scrivi("scena_aperta", scena=tipo, oggetto=oggetto or "-")
        t0 = time.monotonic()
        try:
            ov = self._overlay_pronto()
            if ov is None or not self._scrivi(ov, {"apri": {"sid": sid, "tipo": tipo, "dati": dati,
                                                            "oggetto": file_oggetto}}):
                sc["esito"] = "errore"
            else:
                # da qui i messaggi vanno dritti all'overlay; prima quelli arrivati nel frattempo,
                # in ordine (chi arriva adesso aspetta il lock e passa dopo)
                with self._lock:
                    attesa = self._coda.pop(sid, None)
                    sc["ov"] = ov
                    for d in (attesa or {}).get("righe", []):
                        self._scrivi(ov, d)
                log.info("scena %s mostrata in %.2f s", sid, time.monotonic() - t0)
                if not sc["fine"].wait(SCENA_MAX):
                    self._scrivi(ov, {"chiudi": "scaduta"})
                    sc["fine"].wait(5)
                    sc["esito"] = "scaduta"
        except Exception:  # noqa: BLE001 -- mai far cadere la voce
            log.exception("palco")
        finally:
            with self._lock:
                self._scena = None
                self._coda.pop(sid, None)
        self.registro.scrivi("scena_chiusa", scena=tipo, esito=sc["esito"])
        chiusa(sc["esito"])


# ====================================================================== l'overlay


class _Api:
    """Chiamate dalla pagina (ognuna in un thread suo: inizio() puo' aspettare). Solo
    attributi privati (vedi main.py)."""

    def __init__(self, conn: socket.socket) -> None:
        self._conn = conn
        self._lock = threading.Lock()           # il socket
        self._lock_scena = threading.Lock()     # chiudi() una volta sola anche se chiamata insieme
        self._finestra = None
        self._scena: Optional[dict] = None
        self._presa = False                     # la pagina ha gia' preso la scena (inizio() l'ha data)
        self._arriva = threading.Event()
        self._finito = threading.Event()        # l'overlay si chiude del tutto
        self._dopo_chiusa = None                # fn: nascondi (dal processo)

    def nuova(self, scena: dict) -> None:
        """Arriva una scena: la pagina (ferma in inizio()) la prende."""
        with self._lock_scena:
            self._presa = False
            self._scena = scena
            self._arriva.set()

    def inizio(self) -> Optional[dict]:
        """La pagina e' pronta: aspetta la prossima scena (torna None se l'overlay si chiude)."""
        self._scrivi({"pronto": 1})
        while not self._finito.is_set():
            if not self._arriva.wait(0.5):
                continue
            with self._lock_scena:
                s = self._scena
                if s is None:                   # chiusa prima di prenderla: si aspetta la prossima
                    self._arriva.clear()
                    continue
                self._presa = True
            return {"sid": s.get("sid"), "tipo": s.get("tipo"), "dati": s.get("dati") or {}}
        return None

    def oggetto(self) -> str:
        """Il modello della scena (JSON in chiaro, solo in memoria), "" se non c'e'."""
        f = (self._scena or {}).get("oggetto") or ""
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
        if self._scena is None or not isinstance(ev, str) or not TIPO_RE.match(ev) or ev == "chiusa":
            return False
        return self._scrivi({"ev": ev, "dati": dati if isinstance(dati, dict) else {}})

    def chiudi(self, esito="chiusa") -> Optional[str]:
        """Fine della scena (Esc, la pagina, il server, l'app), una volta sola. Torna il sid se la
        pagina l'aveva gia' presa (allora va ripulita), altrimenti None."""
        with self._lock_scena:
            if self._scena is None:
                return None
            sid, presa = str(self._scena.get("sid") or ""), self._presa
            self._scena = None
            self._presa = False
            self._arriva.clear()
        self._scrivi({"ev": "chiusa", "dati": {"esito": str(esito)[:24]}})
        if not presa:
            self._scrivi({"pronto": 1})         # la pagina e' ancora li' che aspetta
        if self._dopo_chiusa is not None:
            threading.Thread(target=self._dopo_chiusa, daemon=True).start()
        return sid if presa else None

    def _scrivi(self, d: dict) -> bool:
        try:
            with self._lock:
                self._conn.sendall(_riga(d))
            return True
        except OSError:
            return False


def _finestra_mia(titolo: str):
    """La finestra dell'overlay di QUESTO processo (un overlay vecchio che si sta chiudendo ha
    lo stesso titolo: FindWindow potrebbe dare la sua)."""
    from . import cinema as C
    import ctypes
    from ctypes import wintypes

    pid, trovata = os.getpid(), []
    buf = ctypes.create_unicode_buffer(256)

    def cb(hwnd, _l):
        p = wintypes.DWORD()
        C._u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and C._u32.GetWindowTextW(hwnd, buf, 256) and buf.value == titolo:
            trovata.append(hwnd)
            return False
        return True

    C._u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    C._u32.EnumWindows(C._WNDENUMPROC(cb), 0)
    return trovata[0] if trovata else None


def _overlay(porta: int, segreto: str) -> int:
    from . import cinema as C

    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass

    davanti_prima = C._u32.GetForegroundWindow()     # a chi ridare il primo piano se lo rubiamo
    conn = socket.create_connection(("127.0.0.1", porta), timeout=10)
    conn.settimeout(None)
    conn.sendall(_riga({"segreto": segreto}))
    righe = conn.makefile("rb")

    import webview
    api = _Api(conn)
    pagina = os.path.join(cartella_pagine(), "index.html")
    # FUORI SCHERMO: se pywebview la mostra da sola (vedi in cima) non si vede niente
    api._finestra = webview.create_window(TITOLO, pagina, js_api=api, frameless=True, easy_drag=False,
                                          on_top=True, hidden=True, transparent=True, focus=True,
                                          x=FUORI, y=FUORI, width=640, height=480, min_size=(1, 1))
    stato = {"mia": None, "nwn": None, "rett": None, "esclusivo": False}
    lock_vista = threading.Lock()          # mostra / nascondi non si accavallano

    def stile(mia) -> None:
        """Niente icona nella barra e in Alt+Tab, anche nell'attimo in cui pywebview la mostra."""
        s = C._u32.GetWindowLongPtrW(mia, C.GWL_EXSTYLE)
        C._u32.SetWindowLongPtrW(mia, C.GWL_EXSTYLE, (s | C.WS_EX_TOOLWINDOW) & ~C.WS_EX_APPWINDOW)

    def via(mia) -> None:
        C._u32.SetWindowPos(mia, C.HWND_TOPMOST, FUORI, FUORI, 0, 0,
                            C.SWP_NOSIZE | C.SWP_NOACTIVATE)

    def nascondi() -> None:
        """Niente scena: finestra nascosta e fuori schermo, il gioco (o chi c'era) torna davanti."""
        with lock_vista:
            if api._scena is not None:
                return                        # nel frattempo e' arrivata una scena
            try:
                api._finestra.hide()
            except Exception:  # noqa: BLE001
                pass
            mia = stato["mia"]
            if mia:
                via(mia)
                if C._u32.GetForegroundWindow() in (mia, None, 0):
                    # dopo una scena il gioco; al Connetti chi c'era (di solito il Companion)
                    C.porta_davanti(stato["nwn"] or davanti_prima)
    api._dopo_chiusa = nascondi

    def mostra(scena: dict):
        nwn = C.finestra_nwn()
        esclusivo = bool(nwn) and C.nwn_esclusivo()
        rett = None if esclusivo else C.area_di_gioco(nwn)
        if esclusivo:
            C._u32.ShowWindow(nwn, C.SW_MINIMIZE)
        # niente NWN (o schermo intero esclusivo): tutto lo schermo
        pieno = (0, 0, C._u32.GetSystemMetrics(0), C._u32.GetSystemMetrics(1))
        with lock_vista:
            stato.update(nwn=nwn, rett=rett, esclusivo=esclusivo)
            log.info("scena %s (%s), NWN area=%s esclusivo=%s", scena.get("tipo"), scena.get("sid"),
                     rett, esclusivo)
            mia = stato["mia"] = stato["mia"] or _finestra_mia(TITOLO)
            if mia:                           # al suo posto PRIMA di comparire
                stile(mia)
                x, y, w, h = rett or pieno
                C._u32.SetWindowPos(mia, C.HWND_TOPMOST, x, y, w, h, C.SWP_NOACTIVATE)
            api.nuova(scena)
            api._finestra.show()
            if mia:
                C._adatta(mia, rett or pieno)
            C.porta_davanti(mia)

    def ascolta():
        # dall'app: scene da aprire, messaggi dello script per la pagina, "chiudi", "esci"
        def fine_pagina(sid: str):
            try:
                api._finestra.evaluate_js("window.palcoFine && window.palcoFine(%s)" % json.dumps(sid))
            except Exception:  # noqa: BLE001
                pass

        try:
            for riga in righe:
                m = json.loads(riga)
                if m.get("esci"):
                    break
                if isinstance(m.get("apri"), dict):
                    mostra(m["apri"])
                elif m.get("chiudi"):
                    esito = m["chiudi"] if isinstance(m["chiudi"], str) else "server"
                    sid = api.chiudi(esito)
                    if sid:
                        fine_pagina(sid)      # la pagina si ripulisce e torna in attesa
                elif isinstance(m.get("msg"), dict) and api._scena is not None:
                    api._finestra.evaluate_js("window.palcoRicevi && window.palcoRicevi(%s)" % json.dumps(m["msg"]))
        except (OSError, ValueError):
            pass
        api.chiudi("app")                   # l'app se n'e' andata: niente scene orfane
        api._finito.set()
        try:
            api._finestra.destroy()
        except Exception:  # noqa: BLE001
            pass

    def caricata():
        # pywebview l'ha mostrata caricando la pagina: via subito, se non c'e' una scena
        if api._scena is None:
            nascondi()

    api._finestra.events.loaded += caricata

    def guardiano():
        for _ in range(200):
            stato["mia"] = _finestra_mia(TITOLO)
            if stato["mia"]:
                stile(stato["mia"])
                break
            time.sleep(0.05)
        threading.Thread(target=ascolta, daemon=True).start()
        while not api._finito.wait(0.5):
            mia = stato["mia"]
            if not mia:
                continue
            if api._scena is None:
                # rete di sicurezza: comparsa da sola (navigazione, Windows)? si rinasconde
                if C._u32.IsWindowVisible(mia):
                    log.info("overlay visibile senza scena: nascosto")
                    nascondi()
            elif stato["rett"] and not stato["esclusivo"]:
                # resta incollata al gioco mentre c'e' una scena
                C._incolla(mia, C.area_di_gioco(stato["nwn"]) or stato["rett"])

    # private_mode=False + cartella fissa: WebView2 non riparte da zero a ogni avvio (profilo,
    # JavaScript compilato, shader della scheda video restano). Cookie e storage non servono a
    # nessuna pagina del palco.
    webview.start(guardiano, private_mode=False, storage_path=cartella_cache(),
                  http_server=True, http_port=porta_pagine())
    api._finito.set()
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
