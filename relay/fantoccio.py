"""Fantoccio-eco: bot Mumble di TEST per provare l'audio posizionale DA SOLI.

Si connette a Mumble come un finto giocatore ("Fantoccio"), RIASCOLTA quello che
dici e te lo RI-TRASMETTE dopo un ritardo. Il relay gli assegna una posizione
FINTA nel roster la cui DISTANZA da te oscilla (vicino->lontano->vicino in loop),
cosi' stando fermo senti l'eco attraversare di continuo il bordo del raggio --
il punto dove emergono gli artefatti (gracchio).

NON e' per la produzione: si attiva solo col flag --fantoccio del relay.

Audio: pymumble decodifica/codifica con libopus (stessa opus.dll del TalkListener).
Il PCM ricevuto (48kHz, 16-bit, mono) viene ribuffato e ri-immesso tale e quale.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable, Optional

from .talk_listener import _bootstrap_opus


def swept_offset(period: float, maxdist: float) -> float:
    """Distanza che oscilla 0 -> maxdist -> 0 (onda triangolare) col tempo.
    Triangolare = velocita' costante: il bordo del raggio si attraversa sempre
    alla stessa andatura (piu' prevedibile di una sinusoide)."""
    if period <= 0:
        return 0.0
    phase = (time.monotonic() % period) / period      # 0..1
    tri = (2.0 * phase) if phase < 0.5 else (2.0 * (1.0 - phase))   # 0..1..0
    return maxdist * tri


class EchoBot:
    """Bot Mumble che ri-trasmette con ritardo cio' che sente (eco)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 64738,
                 username: str = "Fantoccio", password: str = "",
                 channel: Optional[str] = None, delay: float = 1.5,
                 max_buffer_s: float = 12.0, retry: float = 10.0,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.channel = channel or None
        self.delay = float(delay)
        self.max_buffer_s = float(max_buffer_s)
        self.retry = retry
        self._log = log or (lambda m: print(f"[fantoccio] {m}", flush=True))
        self._buf: deque = deque()          # (play_at_monotonic, pcm_bytes)
        self._lock = threading.Lock()
        self._mumble = None

    # thread audio di pymumble: leggerissimo (solo append con timestamp)
    def _on_sound(self, user, soundchunk) -> None:
        try:
            # non ri-eccheggiare eventuale audio mio (di norma non lo ricevo)
            if user and user.get("name") == self.username:
                return
            pcm = soundchunk.pcm
        except (KeyError, TypeError, AttributeError):
            return
        if not pcm:
            return
        now = time.monotonic()
        with self._lock:
            self._buf.append((now + self.delay, pcm))
            # tetto anti-runaway: scarta il piu' vecchio se la coda esplode
            # (48kHz*2byte = 96000 B/s; max_buffer_s secondi di audio)
            cap = int(self.max_buffer_s * 96000)
            tot = sum(len(p) for _, p in self._buf)
            while self._buf and tot > cap:
                tot -= len(self._buf.popleft()[1])

    def _sender_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            now = time.monotonic()
            out = []
            with self._lock:
                while self._buf and self._buf[0][0] <= now:
                    out.append(self._buf.popleft()[1])
            m = self._mumble
            if m is not None:
                for pcm in out:
                    try:
                        m.sound_output.add_sound(pcm)
                    except Exception:
                        pass
            stop.wait(0.01)

    def _run(self, stop: threading.Event) -> None:
        _bootstrap_opus()
        try:
            from pymumble_py3 import Mumble
            from pymumble_py3.constants import (
                PYMUMBLE_CLBK_SOUNDRECEIVED,
                PYMUMBLE_CONN_STATE_CONNECTED,
            )
        except Exception as exc:
            self._log(f"pymumble/opus non disponibili, fantoccio disattivato: {exc!r}")
            return

        threading.Thread(target=self._sender_loop, args=(stop,), daemon=True).start()
        self._log(f"avvio eco: connessione a {self.host}:{self.port} come "
                  f"{self.username!r} (ritardo {self.delay:.1f}s)")

        while not stop.is_set():
            m = None
            try:
                m = Mumble(self.host, self.username, port=self.port,
                           password=self.password, reconnect=False)
                m.set_receive_sound(True)
                m.callbacks.set_callback(PYMUMBLE_CLBK_SOUNDRECEIVED, self._on_sound)
                m.start()
                m.is_ready()
                if m.connected != PYMUMBLE_CONN_STATE_CONNECTED:
                    raise ConnectionError("murmur non raggiungibile / rifiutata")
                self._mumble = m
                self._log(f"eco connesso a murmur ({self.host}:{self.port})")
                if self.channel:
                    try:
                        m.channels.find_by_name(self.channel).move_in()
                    except Exception:
                        pass
                while not stop.is_set() and m.is_alive() \
                        and m.connected == PYMUMBLE_CONN_STATE_CONNECTED:
                    stop.wait(1.0)
            except Exception as exc:
                self._log(f"eco connessione fallita: {exc!r}")
            finally:
                if m is not None:
                    try:
                        m.stop()
                    except Exception:
                        pass
                self._mumble = None
                with self._lock:
                    self._buf.clear()
            if stop.is_set():
                break
            self._log(f"eco riprovo fra {self.retry:.0f}s")
            stop.wait(self.retry)
        self._log("eco fermato")

    def start(self, stop: threading.Event) -> threading.Thread:
        t = threading.Thread(target=self._run, args=(stop,), daemon=True)
        t.start()
        return t
