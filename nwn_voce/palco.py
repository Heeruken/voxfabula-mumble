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
la scena si nasconde e ricarica la pagina, pronto per la prossima. Protocollo:
    app -> overlay:  {"apri": {sid, tipo, dati, oggetto}}  {"msg": {..}}  {"chiudi": 1}  {"esci": 1}
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


def _riga(d: dict) -> bytes:
    return (json.dumps(d) + "\n").encode("utf-8")


# ====================================================================== nell'app


class Palco:
    """Una scena alla volta. Chiamato dal thread del relay: ogni metodo torna subito."""

    def __init__(self, registro) -> None:
        self.registro = registro
        self._lock = threading.Lock()          # la scena in corso
        self._ov_lock = threading.Lock()       # l'overlay (avvio)
        self._ov: Optional[dict] = None        # {proc, conn, pronto, vivo, invio}
        self._scena: Optional[dict] = None     # {sid, client, fine, esito}

    # -- dal motore
    def prepara(self) -> None:
        """Al Connetti: l'overlay parte nascosto e resta pronto (la prima scena compare subito)."""
        if palco_attivo():
            threading.Thread(target=self._overlay_pronto, name="palco-prepara", daemon=True).start()

    def chiudi_tutto(self) -> None:
        """L'app si chiude: via l'overlay."""
        with self._ov_lock:
            ov, self._ov = self._ov, None
        if ov is None:
            return
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
            threading.Thread(target=self._apri, args=(sid, tipo, dati, client),
                             name="palco", daemon=True).start()
        elif tipo_msg == "msg":
            self._alla_scena(sid, {"msg": dati})
        elif tipo_msg == "chiudi":
            self._alla_scena(sid, {"chiudi": 1})

    def _alla_scena(self, sid: str, d: dict) -> None:
        with self._lock:
            sc = self._scena
        if sc is None or sc["sid"] != sid or self._ov is None:
            return
        self._scrivi(self._ov, d)

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
                ov = self._avvia_overlay()
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
                if sc is None:
                    continue
                if ev == "chiusa":
                    ov["pronto"].clear()                  # ricarica la pagina: un attimo e torna pronto
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
        if sc is not None and not sc["fine"].is_set():
            sc["esito"] = "errore"
            sc["fine"].set()

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
        sc = {"sid": sid, "client": client, "fine": threading.Event(), "esito": "errore"}
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
                log.info("scena %s mostrata in %.2f s", sid, time.monotonic() - t0)
                if not sc["fine"].wait(SCENA_MAX):
                    self._scrivi(ov, {"chiudi": 1})
                    sc["fine"].wait(5)
                    sc["esito"] = "scaduta"
        except Exception:  # noqa: BLE001 -- mai far cadere la voce
            log.exception("palco")
        finally:
            with self._lock:
                self._scena = None
        self.registro.scrivi("scena_chiusa", scena=tipo, esito=sc["esito"])
        chiusa(sc["esito"])


# ====================================================================== l'overlay


class _Api:
    """Chiamate dalla pagina (ognuna in un thread suo: inizio() puo' aspettare). Solo
    attributi privati (vedi main.py)."""

    def __init__(self, conn: socket.socket) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self._finestra = None
        self._scena: Optional[dict] = None
        self._arriva = threading.Event()
        self._finito = threading.Event()        # l'overlay si chiude del tutto
        self._dopo_chiusa = None                # fn: nascondi + ricarica (dal processo)

    def inizio(self) -> Optional[dict]:
        """La pagina e' pronta: aspetta la prossima scena (torna None se l'overlay si chiude)."""
        self._scrivi({"pronto": 1})
        while not self._finito.is_set():
            if self._arriva.wait(0.5):
                break
        s = self._scena
        if s is None:
            return None
        return {"sid": s.get("sid"), "tipo": s.get("tipo"), "dati": s.get("dati") or {}}

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

    def chiudi(self, esito="chiusa") -> None:
        if self._scena is None:
            return
        self._scena = None
        self._arriva.clear()
        self._scrivi({"ev": "chiusa", "dati": {"esito": str(esito)[:24]}})
        if self._dopo_chiusa is not None:
            threading.Thread(target=self._dopo_chiusa, daemon=True).start()

    def _scrivi(self, d: dict) -> bool:
        try:
            with self._lock:
                self._conn.sendall(_riga(d))
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
    conn.settimeout(None)
    conn.sendall(_riga({"segreto": segreto}))
    righe = conn.makefile("rb")

    import webview
    api = _Api(conn)
    pagina = os.path.join(cartella_pagine(), "index.html")
    api._finestra = webview.create_window(TITOLO, pagina, js_api=api, frameless=True, easy_drag=False,
                                          on_top=True, hidden=True, transparent=True, focus=True)
    stato = {"mia": None, "nwn": None, "rett": None, "esclusivo": False}

    def nascondi_e_ricarica():
        try:
            api._finestra.hide()
        except Exception:  # noqa: BLE001
            pass
        if stato["nwn"]:
            C.porta_davanti(stato["nwn"])
        time.sleep(0.2)
        try:
            api._finestra.evaluate_js("location.reload()")     # pagina pulita, pronta per la prossima
        except Exception:  # noqa: BLE001
            pass
    api._dopo_chiusa = nascondi_e_ricarica

    def mostra(scena: dict):
        nwn = C.finestra_nwn()
        esclusivo = bool(nwn) and C.nwn_esclusivo()
        rett = None if esclusivo else C.area_di_gioco(nwn)
        if esclusivo:
            C._u32.ShowWindow(nwn, C.SW_MINIMIZE)
        stato.update(nwn=nwn, rett=rett, esclusivo=esclusivo)
        log.info("scena %s (%s), NWN area=%s esclusivo=%s", scena.get("tipo"), scena.get("sid"), rett, esclusivo)
        api._scena = scena
        api._arriva.set()
        mia = stato["mia"] or C._u32.FindWindowW(None, TITOLO)
        stato["mia"] = mia
        api._finestra.show()
        if mia:
            if rett is None:        # niente NWN (o schermo intero esclusivo): tutto lo schermo
                w, h = C._u32.GetSystemMetrics(0), C._u32.GetSystemMetrics(1)
                C._adatta(mia, (0, 0, w, h))
            else:
                C._adatta(mia, rett)
        C.porta_davanti(mia)

    def ascolta():
        # dall'app: scene da aprire, messaggi dello script per la pagina, "chiudi", "esci"
        try:
            for riga in righe:
                m = json.loads(riga)
                if m.get("esci"):
                    break
                if isinstance(m.get("apri"), dict):
                    mostra(m["apri"])
                elif m.get("chiudi"):
                    api.chiudi("server")
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

    def guardiano():
        for _ in range(50):
            stato["mia"] = C._u32.FindWindowW(None, TITOLO)
            if stato["mia"]:
                break
            time.sleep(0.05)
        threading.Thread(target=ascolta, daemon=True).start()
        while not api._finito.wait(0.5):
            # resta incollata al gioco mentre c'e' una scena
            if api._scena is not None and stato["mia"] and stato["rett"] and not stato["esclusivo"]:
                C._incolla(stato["mia"], C.area_di_gioco(stato["nwn"]) or stato["rett"])

    webview.start(guardiano, private_mode=True, http_server=True)
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
