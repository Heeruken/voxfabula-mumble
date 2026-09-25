"""Connessione al relay del server (porta 27890).

Il relay legge dal database del server NWN dove sta ogni personaggio e manda a
questo client, in continuo:
  - lo stato del NOSTRO personaggio (posizione, area, sguardo),
  - il roster di tutti (nome, area, posizione, sussurra/parla/urla),
che il plugin vc_range usa per regolare il volume di ogni voce.

Si riconnette da solo se la connessione cade. Protocollo: vedi net_protocol.py
(NON cambiarlo senza aggiornare anche il relay: i giocatori con l'app vecchia
devono continuare a funzionare).
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Optional

from . import net_protocol as proto
from .state import PlayerState

log = logging.getLogger(__name__)


class RelayClient:
    def __init__(self, host: str, port: int = 27890, player: str = "",
                 token: Optional[str] = None, stale_after: float = 2.0) -> None:
        self.host = host
        self.port = port
        self.player = player
        self.token = token
        self.stale_after = stale_after
        self._lock = threading.Lock()
        self._latest: Optional[PlayerState] = None
        self._last_recv = 0.0
        self._roster: list = []
        self._connected = False
        self._rejected: Optional[str] = None
        self._stop = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

    # ---- ciclo di vita ----
    def open(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="relay-client", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            sock = self._sock
        if sock is not None:
            for fn in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
                try:
                    fn()
                except OSError:
                    pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ---- stato letto dal ponte ----
    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def rejected(self) -> Optional[str]:
        with self._lock:
            return self._rejected

    def poll(self) -> Optional[PlayerState]:
        """Il nostro personaggio, o None se non siamo in gioco / dati vecchi."""
        with self._lock:
            st, last = self._latest, self._last_recv
        if st is not None and (time.monotonic() - last) <= self.stale_after:
            return st
        return None

    def read_roster(self) -> list:
        with self._lock:
            return list(self._roster)

    # ---- rete ----
    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5.0)
                sock.settimeout(None)
                with self._lock:
                    self._sock = sock
                    self._connected = True
                log.info("relay %s:%s connesso", self.host, self.port)
                backoff = 1.0
                sock.sendall(proto.encode_hello(self.player, self.token))
                for line in sock.makefile("rb"):
                    if self._stop.is_set():
                        break
                    try:
                        msg = proto.decode_line(line)
                    except ValueError:
                        continue
                    now = time.monotonic()
                    if "x" in msg:
                        st = proto.state_from_msg(msg)
                        with self._lock:
                            self._latest, self._last_recv = st, now
                    elif "roster" in msg:
                        r = proto.roster_from_msg(msg)
                        with self._lock:
                            self._roster = r
                    elif "error" in msg:
                        log.warning("il relay ci ha rifiutato: %r", msg["error"])
                        with self._lock:
                            self._rejected = str(msg["error"])
                        break
                    else:  # idle: connessi ma personaggio non in gioco
                        with self._lock:
                            self._latest, self._last_recv = None, now
            except OSError as exc:
                log.info("relay %s:%s non raggiungibile: %s", self.host, self.port, exc)
            finally:
                with self._lock:
                    self._latest = None
                    self._connected = False
                    if self._sock is not None:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
            if self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 10.0)
