"""Ascoltatore Mumble lato server: il "bot" che sa CHI parla.

Si connette a murmur (il server Mumble) in localhost come client senza finestra,
sta zitto nel canale e ascolta. Quando Mumble gli consegna audio di un utente
(``sound_received``) sa che quell'utente sta parlando; quando l'audio smette per
un attimo, ha smesso. Niente VAD separato sul microfono: ci appoggiamo alla
rilevazione di Mumble stesso (trasmette = parla), che e' anche cio' che gli altri
sentono -> l'icona combacia con la voce.

Per ogni transizione parla/non-parla chiama ``on_change(nome_mumble, parla)``.
Nel relay quel callback e' ``_TalkWriter.set_talking``, che traduce il nome in
CD key e scrive ``vc_client.talking`` -> l'icona in gioco appare a tutti.

Robustezza:
- gira in un thread daemon, con riconnessione automatica (murmur non ancora su,
  riavviato, ecc.): non blocca mai il resto del relay;
- il callback audio fa solo un timestamp (velocissimo); le scritture su DB
  avvengono solo sulle TRANSIZIONI, in un thread separato (sweep);
- alla disconnessione spegne tutte le icone rimaste accese (niente icone
  "congelate").

Opus: pymumble decodifica con libopus. Su Windows la DLL non c'e' di default, la
forniamo noi in ``bridge/vendor/opus.dll`` (binario ufficiale Xiph, BSD). La
mettiamo nel PATH PRIMA di importare pymumble cosi' ``ctypes.util.find_library``
la trova.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable, Dict, Optional


def _bootstrap_opus() -> None:
    """Rende ``opus.dll`` trovabile da opuslib/pymumble (su Windows e' caricata via
    ``ctypes.util.find_library``, che cerca nel PATH). Cerca la DLL in piu' posizioni
    cosi' funziona sia da sorgente sia nei bundle PyInstaller (onefile _MEIPASS /
    onedir _internal)."""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, "vendor")]
    mei = getattr(sys, "_MEIPASS", None)         # PyInstaller: cartella di estrazione
    if mei:
        cands.append(os.path.join(mei, "bridge", "vendor"))
        cands.append(os.path.join(mei, "vendor"))
    for vendor in cands:
        if os.path.isdir(vendor):
            os.environ["PATH"] = vendor + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(vendor)     # py3.8+: risolve eventuali dipendenze
            except (OSError, AttributeError):
                pass
            return


class TalkListener:
    """Bot Mumble headless che riporta chi sta parlando via ``on_change``.

    on_change(name: str, talking: bool) -- chiamato SOLO sulle transizioni.
    """

    def __init__(self, on_change: Callable[[str, bool], None],
                 host: str = "127.0.0.1", port: int = 64738,
                 username: str = "NWN-Voce", password: str = "",
                 channel: Optional[str] = None,
                 certfile: Optional[str] = None, keyfile: Optional[str] = None,
                 off_after: float = 0.25, sweep: float = 0.02,
                 retry: float = 10.0,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self.on_change = on_change
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.channel = channel or None
        self.certfile = certfile
        self.keyfile = keyfile
        self.off_after = off_after        # silenzio oltre questo -> "ha smesso"
        self.sweep = sweep                # ogni quanto rivaluto lo stato
        self.retry = retry                # attesa fra due tentativi di connessione
        self._log = log or (lambda m: print(f"[talk] {m}", flush=True))

        self._last_audio: Dict[str, float] = {}   # nome -> monotonic ultimo frame
        self._talking: Dict[str, bool] = {}       # nome -> ultimo stato riportato
        self._lock = threading.Lock()
        self._mumble = None

    # --- thread audio di pymumble: tenere LEGGERISSIMO -----------------------
    def _on_sound(self, user, soundchunk) -> None:
        try:
            nm = user["name"]
        except (KeyError, TypeError):
            return
        if not nm:
            return
        with self._lock:
            self._last_audio[nm] = time.monotonic()

    # --- thread separato: decide transizioni e scrive (raro) ----------------
    def _sweep_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            now = time.monotonic()
            changes = []
            with self._lock:
                for nm in list(self._last_audio.keys()):
                    silent = now - self._last_audio[nm]
                    talking = silent <= self.off_after
                    if self._talking.get(nm, False) != talking:
                        self._talking[nm] = talking
                        changes.append((nm, talking))
                    if not talking and silent > 30.0:    # pulizia: nome muto da un po'
                        self._last_audio.pop(nm, None)
                        self._talking.pop(nm, None)
            for nm, t in changes:
                self._log(f"{'PARLA' if t else 'tace '} -> {nm}")   # visibile nel log del relay
                try:
                    self.on_change(nm, t)
                except Exception as exc:                 # un errore DB non deve fermare lo sweep
                    self._log(f"on_change({nm!r},{t}) errore: {exc!r}")
            stop.wait(self.sweep)

    def _clear_all(self) -> None:
        """Spegne ogni icona accesa (es. alla disconnessione del bot)."""
        with self._lock:
            on = [nm for nm, t in self._talking.items() if t]
            self._last_audio.clear()
            self._talking.clear()
        for nm in on:
            try:
                self.on_change(nm, False)
            except Exception:
                pass

    # --- ciclo di connessione (con riconnessione) ---------------------------
    def _run(self, stop: threading.Event) -> None:
        _bootstrap_opus()
        try:
            from pymumble_py3 import Mumble
            from pymumble_py3.constants import (
                PYMUMBLE_CLBK_SOUNDRECEIVED,
                PYMUMBLE_CONN_STATE_CONNECTED,
            )
        except Exception as exc:
            self._log(f"pymumble/opus non disponibili, bot disattivato: {exc!r}")
            return

        threading.Thread(target=self._sweep_loop, args=(stop,), daemon=True).start()
        self._log(f"avvio: ascolto chi parla su {self.host}:{self.port} "
                  f"come {self.username!r}")

        while not stop.is_set():
            m = None
            try:
                m = Mumble(self.host, self.username, port=self.port,
                           password=self.password, certfile=self.certfile,
                           keyfile=self.keyfile, reconnect=False)
                m.set_receive_sound(True)
                m.callbacks.set_callback(PYMUMBLE_CLBK_SOUNDRECEIVED, self._on_sound)
                m.start()
                m.is_ready()                     # blocca fino a connesso o fallito
                if m.connected != PYMUMBLE_CONN_STATE_CONNECTED:
                    raise ConnectionError("murmur non raggiungibile / rifiutata")
                self._mumble = m
                self._log(f"connesso a murmur ({self.host}:{self.port})")
                if self.channel:
                    try:
                        m.channels.find_by_name(self.channel).move_in()
                        self._log(f"entrato nel canale {self.channel!r}")
                    except Exception as exc:
                        self._log(f"canale {self.channel!r} non trovato "
                                  f"({exc!r}); resto in root")
                # resta connesso finche' la thread Mumble vive e non ci fermano
                while not stop.is_set() and m.is_alive() \
                        and m.connected == PYMUMBLE_CONN_STATE_CONNECTED:
                    stop.wait(1.0)
            except Exception as exc:
                self._log(f"connessione fallita: {exc!r}")
            finally:
                if m is not None:
                    try:
                        m.stop()
                    except Exception:
                        pass
                self._mumble = None
                self._clear_all()                # niente icone congelate
            if stop.is_set():
                break
            self._log(f"riprovo fra {self.retry:.0f}s")
            stop.wait(self.retry)
        self._log("fermato")

    def start(self, stop: threading.Event) -> threading.Thread:
        t = threading.Thread(target=self._run, args=(stop,), daemon=True)
        t.start()
        return t
