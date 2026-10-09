"""La regia: riceve dal relay le richieste di video e le passa al cinema.

Il server (uno script del modulo) chiede "mostra il video X a questo giocatore".
Qui decidiamo SE mostrarlo e lo facciamo in un PROCESSO SEPARATO (cinema.py):
se il lettore si pianta, la voce non ne risente. L'esito torna al relay, e da li'
allo script, che intanto tiene il personaggio fermo e al sicuro.

Ogni cosa che succede finisce nel registro attivita' (attivita.log), che il
giocatore vede nella finestra: niente avviene di nascosto sul suo PC.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from . import paths, settings

log = logging.getLogger(__name__)

NOME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
ESTENSIONI = (".vfv", ".webm", ".mp4")   # .vfv = sigillato (sigillo.py), quello pubblicato
# esito del processo lettore (cinema.py) -> esito per il server
_ESITI = {0: "visto", 3: "saltato", 2: "mancante"}
REGISTRO_MAX = 200          # righe tenute nel registro attivita'


def cartella_video() -> str:
    d = os.path.join(paths.data_dir(), "video")
    os.makedirs(d, exist_ok=True)
    return d


def trova_video(nome: str) -> Optional[str]:
    """Il file del video ``nome`` nella cartella video, o None."""
    if not NOME_RE.match(nome or ""):
        return None
    for est in ESTENSIONI:
        p = os.path.join(cartella_video(), nome + est)
        if os.path.isfile(p):
            return p
    return None


def video_attivi() -> bool:
    """Interruttore "Video del server" (acceso di serie)."""
    return settings.load().get("video_server", True) is not False


class Registro:
    """Registro attivita' visibile al giocatore: in memoria + file attivita.log."""

    def __init__(self, emit: Callable[..., None]) -> None:
        self.emit = emit
        self._lock = threading.Lock()
        self._righe: list = []
        self._file = os.path.join(paths.data_dir(), "attivita.log")
        try:
            with open(self._file, encoding="utf-8") as fh:
                self._righe = [r.rstrip("\n") for r in fh][-REGISTRO_MAX:]
        except OSError:
            pass

    def scrivi(self, chiave: str, **dati) -> None:
        """``chiave`` e' tradotta dalla pagina; nel file va una riga leggibile."""
        ora = time.strftime("%d/%m %H:%M")
        riga = f"{ora}  {chiave}  " + "  ".join(f"{k}={v}" for k, v in dati.items())
        with self._lock:
            self._righe = (self._righe + [riga])[-REGISTRO_MAX:]
            try:
                with open(self._file, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(self._righe) + "\n")
            except OSError:
                pass
        self.emit("attivita", ora=ora, voce=chiave, **dati)

    def righe(self) -> list:
        with self._lock:
            return list(self._righe)


class Regia:
    def __init__(self, registro: Registro) -> None:
        self.registro = registro
        self._lock = threading.Lock()
        self._in_corso: Optional[str] = None

    def richiesta(self, nome: str, rid: str, rispondi: Callable[[str, str], bool]) -> None:
        """Chiamata dal thread del relay: deve tornare subito."""
        threading.Thread(target=self._esegui, args=(nome, rid, rispondi),
                         name="regia", daemon=True).start()

    def _esegui(self, nome: str, rid: str, rispondi) -> None:
        esito = self._mostra(nome, rid)
        self.registro.scrivi("video_esito", video=nome, esito=esito)
        if not rispondi(rid, esito):
            log.warning("esito del video %s non consegnato (relay scollegato)", rid)

    def _mostra(self, nome: str, rid: str) -> str:
        if not video_attivi():
            self.registro.scrivi("video_rifiutato", video=nome)
            return "disattivato"
        file = trova_video(nome)
        if file is None and NOME_RE.match(nome or ""):
            # non ancora scaricato (pubblicato dopo il Connetti): un tentativo adesso, come il palco
            try:
                from . import videoteca
                videoteca.aggiorna(self.registro)
            except Exception:  # noqa: BLE001
                log.exception("videoteca")
            file = trova_video(nome)
        if file is None:
            self.registro.scrivi("video_mancante", video=nome)
            return "mancante"
        with self._lock:
            if self._in_corso is not None:
                return "occupato"
            self._in_corso = rid
        try:
            self.registro.scrivi("video_inizio", video=nome)
            return self._lettore(file)
        finally:
            with self._lock:
                self._in_corso = None

    @staticmethod
    def _lettore(file: str) -> str:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--video", file]
        else:
            cmd = [sys.executable, "-m", "nwn_voce", "--video", file]
        try:
            p = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               timeout=60 * 30, capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            log.exception("lettore video")
            return "errore"
        return _ESITI.get(p.returncode, "errore")
