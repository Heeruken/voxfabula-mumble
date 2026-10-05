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
                 token: Optional[str] = None, stale_after: float = 2.0,
                 on_video=None, on_scena=None) -> None:
        self.host = host
        self.port = port
        self.player = player
        self.token = token
        self.stale_after = stale_after
        # on_video(nome, id): il server chiede un video. Se c'e', il hello dice al relay
        # che sappiamo mostrarli; deve ritornare subito (il lavoro va in un altro thread).
        self.on_video = on_video
        # on_scena(tipo_msg, sid, tipo, dati): il palco (scene interattive). tipo_msg e'
        # "apri" | "msg" | "chiudi" | "perso" (collegamento caduto: il relay ha gia' chiuso le
        # scene aperte, "disconnesso"; sid vuoto). Come on_video: deve ritornare subito.
        self.on_scena = on_scena
        self._send_lock = threading.Lock()
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

    def send_video_esito(self, rid: str, esito: str) -> bool:
        """Manda l'esito di un video al relay. False se in questo momento non siamo
        collegati (lo script in gioco ha comunque i suoi tempi massimi)."""
        with self._lock:
            sock = self._sock
        if sock is None:
            return False
        try:
            with self._send_lock:
                sock.sendall(proto.encode_video_esito(rid, esito))
            return True
        except OSError:
            return False

    def send_scena_ev(self, sid: str, ev: str, dati: Optional[dict] = None) -> bool:
        """Un evento del giocatore nella scena ``sid`` (False = non collegati)."""
        with self._lock:
            sock = self._sock
        if sock is None:
            return False
        try:
            with self._send_lock:
                sock.sendall(proto.encode_scena_ev(sid, ev, dati))
            return True
        except OSError:
            return False

    def _scena(self, tipo_msg: str, msg: dict, chiave: str) -> None:
        sid = msg.get(chiave)
        if self.on_scena is None or not proto.sid_ok(sid):
            return
        dati = msg.get("dati", {})
        try:
            self.on_scena(tipo_msg, sid, str(msg.get("tipo", "")), dati if isinstance(dati, dict) else {})
        except Exception:  # noqa: BLE001 -- mai far cadere la voce
            log.exception("scena non gestita")

    # ---- rete ----
    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            collegato = False
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5.0)
                sock.settimeout(None)
                with self._lock:
                    self._sock = sock
                    self._connected = True
                log.info("relay %s:%s connesso", self.host, self.port)
                collegato = True
                backoff = 1.0
                with self._send_lock:
                    sock.sendall(proto.encode_hello(self.player, self.token,
                                                    cinema=self.on_video is not None,
                                                    palco=self.on_scena is not None))
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
                    elif "video" in msg:
                        if self.on_video is not None:
                            try:
                                self.on_video(str(msg["video"]), str(msg.get("id", "")))
                            except Exception:  # noqa: BLE001 -- mai far cadere la voce
                                log.exception("richiesta video non gestita")
                    elif "scena_msg" in msg:
                        self._scena("msg", msg, "scena_msg")
                    elif "scena" in msg:
                        self._scena("apri", msg, "scena")
                    elif "scena_chiudi" in msg:
                        self._scena("chiudi", msg, "scena_chiudi")
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
            if collegato and self.on_scena is not None:
                try:
                    self.on_scena("perso", "", "", {})
                except Exception:  # noqa: BLE001 -- mai far cadere la voce
                    log.exception("scena: collegamento perso non gestito")
            if self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 10.0)
