"""Il motore: cosa succede quando premi Connetti e Ferma.

Connetti:
  1. prepara le impostazioni del NOSTRO Mumble (mumble_config.py);
  2. se e' rimasto aperto un NOSTRO Mumble da una sessione precedente, lo chiude
     (cercandolo per percorso: il Mumble dell'utente non viene mai toccato);
  3. avvia il Mumble portatile, minimizzato, collegato al server vocale;
  4. si collega al relay e avvia il ponte in un thread di questo stesso programma.
Ferma: ferma il ponte e chiude il nostro Mumble (con garbo, cosi' salva).

Il motore non conosce le lingue: manda CHIAVI di stato che la finestra traduce.
Tutto questo gira in thread, mai nel thread della finestra.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Callable, Optional
from urllib.parse import quote

from . import bridge, paths, settings, winproc
from .mumble_config import accept_server_cert, write_config
from .relay_client import RelayClient

log = logging.getLogger(__name__)

RELAY_PORT = 27890
MUMBLE_PORT = 64738
RELAY_WARN_AFTER = 10.0     # secondi senza relay prima di avvisare
SW_SHOWMINNOACTIVE = 7      # minimizzato, senza rubare il focus a NWN


class Engine:
    def __init__(self, emit: Optional[Callable[..., None]] = None) -> None:
        self.emit = emit or (lambda key, **data: None)
        self._lock = threading.RLock()
        self._gen = 0
        self._mumble: Optional[subprocess.Popen] = None
        self._client: Optional[RelayClient] = None
        self._stop_evt: Optional[threading.Event] = None
        self._bridge_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ API
    def start(self, host: str, name: str) -> None:
        with self._lock:
            self._shutdown()
            self._gen += 1
            gen = self._gen
            self.emit("client_start", name=name, host=host)

            mdir = paths.mumble_dir()
            if not mdir:
                self.emit("mumble_missing")
                return
            exe = os.path.join(mdir, "mumble.exe")

            for pid in winproc.pids_for_image(exe):          # nostri avanzi, non dell'utente
                log.info("chiudo un nostro Mumble rimasto aperto (pid %s)", pid)
                winproc.close_process(pid)

            s = settings.load()
            try:
                cfg = write_config(mdir, input_device=s.get("input_device") or None,
                                   output_device=s.get("output_device") or None,
                                   transmit=s.get("transmit") or "ptt",
                                   ptt_key=s.get("ptt_key"))
            except Exception as exc:  # noqa: BLE001
                log.exception("configurazione Mumble fallita")
                self.emit("config_err", err=str(exc))
                return
            self.emit("config_ok")
            self._stash_crash_dump()
            try:
                accept_server_cert(host, MUMBLE_PORT)
            except Exception:  # noqa: BLE001 -- nel peggiore dei casi Mumble chiede lui
                log.exception("pre-accettazione del certificato non riuscita")

            url = f"mumble://{quote(name, safe='')}@{host}:{MUMBLE_PORT}/"
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = SW_SHOWMINNOACTIVE
            try:
                self._mumble = subprocess.Popen([exe, "-m", "-c", cfg, url], cwd=mdir, startupinfo=si)
            except OSError as exc:
                log.exception("avvio Mumble fallito")
                self.emit("fatal", err=str(exc))
                return
            log.info("Mumble avviato (pid %s) verso %s", self._mumble.pid, host)
            self.emit("mumble_launched", host=host)

            client = RelayClient(host=host, port=RELAY_PORT, player=name)
            client.open()
            stop = threading.Event()
            self._client, self._stop_evt = client, stop
            self._bridge_thread = threading.Thread(
                target=self._bridge_main, args=(client, stop, gen), name="bridge", daemon=True)
            self._bridge_thread.start()
            threading.Thread(target=self._watch, args=(client, stop, gen, host),
                             name="watch", daemon=True).start()
            self.emit("ready_client")

    def stop(self, quiet: bool = False) -> None:
        with self._lock:
            self._shutdown()
            if not quiet:
                self.emit("stopped")

    # -------------------------------------------------------------- interni
    @staticmethod
    def _stash_crash_dump() -> None:
        """Se Mumble e' crashato in passato ha lasciato mumble.dmp: al prossimo avvio
        mostrerebbe la sua finestra "Rapporto errori". La mettiamo da parte in
        crash\\ (utile per capire il problema) invece di disturbare il giocatore."""
        dmp = os.path.join(paths.data_dir(), "mumble.dmp")
        if os.path.exists(dmp):
            dest = os.path.join(paths.data_dir(), "crash")
            os.makedirs(dest, exist_ok=True)
            os.replace(dmp, os.path.join(dest, time.strftime("mumble-%Y%m%d-%H%M%S.dmp")))
            log.warning("trovato un crash di Mumble precedente: spostato in %s", dest)

    def _emit_if(self, gen: int, key: str, **data) -> None:
        if gen == self._gen:           # messaggi di una sessione vecchia: zitti
            self.emit(key, **data)

    def _bridge_main(self, client: RelayClient, stop: threading.Event, gen: int) -> None:
        try:
            bridge.run(client, stop,
                       on_live=lambda live: self._emit_if(gen, "live" if live else "connecting"))
        except Exception as exc:  # noqa: BLE001
            log.exception("il ponte si e' fermato")
            if not stop.is_set():
                self._emit_if(gen, "bridge_error", err=str(exc))

    def _watch(self, client: RelayClient, stop: threading.Event, gen: int, host: str) -> None:
        """Dice la verita' su cosa non va: relay irraggiungibile, rifiuto, Mumble chiuso."""
        started = time.monotonic()
        relay_state = None            # None / "ok" / "down"
        mumble_warned = False
        rejected_warned = False
        while not stop.wait(1.0):
            if gen != self._gen:
                return
            if client.connected:
                if relay_state != "ok":
                    relay_state = "ok"
                    self._emit_if(gen, "relay_ok")
            elif time.monotonic() - started > RELAY_WARN_AFTER and relay_state != "down":
                relay_state = "down"
                self._emit_if(gen, "relay_unreachable", host=host, port=RELAY_PORT)
            if client.rejected and not rejected_warned:
                rejected_warned = True
                self._emit_if(gen, "relay_rejected", err=client.rejected)
            m = self._mumble
            if m is not None and m.poll() is not None and not mumble_warned:
                mumble_warned = True
                self._emit_if(gen, "mumble_closed")

    def _shutdown(self) -> None:
        """Ferma tutto quello che ABBIAMO avviato noi. Rientrante."""
        self._gen += 1
        if self._stop_evt is not None:
            self._stop_evt.set()
        if self._bridge_thread is not None:
            self._bridge_thread.join(timeout=3.0)
        if self._client is not None:
            self._client.close()
        if self._mumble is not None and self._mumble.poll() is None:
            log.info("chiudo il nostro Mumble (pid %s)", self._mumble.pid)
            winproc.close_process(self._mumble.pid)
        self._mumble = self._client = self._stop_evt = self._bridge_thread = None
