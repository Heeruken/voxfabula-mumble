"""NWN Voce - la finestra (pywebview + interfaccia HTML).

Regola anti-crash: la finestra si tocca SOLO dal suo thread. Il motore gira nei
thread e si limita ad ACCODARE chiavi di stato; la pagina le legge con poll().
Ogni metodo chiamato dalla pagina ritorna subito.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback

from . import APP_NAME, __version__, paths, settings, winproc
from .audio_devices import list_audio_devices
from .engine import Engine

log = logging.getLogger("nwn_voce")


def _setup_logging() -> None:
    handler = logging.FileHandler(paths.log_file(), mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    def hook(exctype, value, tb):
        log.error("errore non gestito:\n%s", "".join(traceback.format_exception(exctype, value, tb)))

    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)


class Api:
    """Metodi chiamati dalla pagina (window.pywebview.api.*)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._statuses: list = []
        self._devices = {"input": [], "output": []}
        self.engine = Engine(emit=self._queue)
        threading.Thread(target=self._load_devices, daemon=True).start()

    def _load_devices(self) -> None:
        d = list_audio_devices()
        with self._lock:
            self._devices = d

    def _queue(self, key: str, **data) -> None:
        log.info("stato: %s %s", key, data or "")
        with self._lock:
            self._statuses.append({"key": key, "data": data})

    def _spawn(self, fn, *args) -> None:
        def run():
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001
                log.exception("errore nel motore")
                self._queue("fatal", err=str(exc))
        threading.Thread(target=run, daemon=True).start()

    # ---- chiamate dalla pagina ----
    def poll(self) -> dict:
        with self._lock:
            items, self._statuses = self._statuses, []
        return {"status": items}

    def get_settings(self) -> dict:
        d = settings.load()
        d["version"] = __version__
        return d

    def get_audio_devices(self) -> dict:
        with self._lock:
            return self._devices

    def save_audio(self, name, input_device, output_device) -> dict:
        kv = {"input_device": (input_device or "").strip(),
              "output_device": (output_device or "").strip()}
        if name is not None and name.strip():
            kv["name"] = name.strip()
        settings.update(**kv)
        return {"ok": True}

    def save_lang(self, lang) -> bool:
        settings.update(lang=lang)
        return True

    def start(self, host, name) -> dict:
        host, name = (host or "").strip(), (name or "").strip()
        if not host:
            self._queue("need_ip")
            return {"ok": False}
        if not name:
            self._queue("need_name")
            return {"ok": False}
        settings.update(host=host, name=name)
        self._spawn(self.engine.start, host, name)
        return {"ok": True}

    def stop(self) -> dict:
        self._spawn(self.engine.stop)
        return {"ok": True}


def main() -> None:
    if not winproc.single_instance():
        winproc.message_box(f"{APP_NAME} è già aperto.", APP_NAME)
        return
    _setup_logging()
    log.info("%s %s avviato", APP_NAME, __version__)

    import webview  # dopo il controllo istanza: avvio piu' rapido se gia' aperto

    api = Api()
    webview.create_window(APP_NAME, paths.web_index(), js_api=api,
                          width=560, height=780, min_size=(460, 640),
                          background_color="#12100c")
    webview.start()
    # finestra chiusa: fermiamo ponte e Mumble prima di uscire
    api.engine.stop(quiet=True)
    log.info("chiuso")


if __name__ == "__main__":
    main()
