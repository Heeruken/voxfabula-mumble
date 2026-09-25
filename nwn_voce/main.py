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

import webbrowser

from . import APP_NAME, __version__, paths, settings, updater, winproc
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
        self._update: updater.Update | None = None
        self._last_pct = -1
        # NB: solo attributi con "_": pywebview espone alla pagina (ed esplora
        # ricorsivamente) tutto cio' che e' pubblico. Esplorare la finestra dal suo
        # stesso avvio la blocca: niente pagina collegata, niente chiusura.
        self._pending_installer: str | None = None   # lanciato da main() dopo la chiusura
        self._window = None
        self._engine = Engine(emit=self._queue)
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

    def save_voice(self, transmit, key) -> dict:
        """Modalita' ("ptt"/"vad") e tasto del push-to-talk scelti nell'app.
        key = {"kind": "keyboard", "code": "CapsLock"} o {"kind": "mouse", "button": 3}."""
        from . import mumble_keys
        transmit = "vad" if transmit == "vad" else "ptt"
        if not isinstance(key, dict) or not mumble_keys.is_supported(key):
            return {"ok": False}
        settings.update(transmit=transmit, ptt_key=key)
        return {"ok": True}

    def save_lang(self, lang) -> bool:
        settings.update(lang=lang)
        return True

    # ---- aggiornamenti ----
    def check_update(self) -> bool:
        def work():
            upd = updater.check()
            if upd is None:
                return
            with self._lock:
                self._update = upd
            self._queue("update_available", version=upd.version, notes=upd.notes,
                        mandatory=upd.mandatory, auto=updater.is_installed())
        threading.Thread(target=work, daemon=True).start()
        return True

    def update_now(self) -> dict:
        with self._lock:
            upd = self._update
        if upd is None:
            return {"ok": False}
        if not updater.is_installed():
            # zip portatile o sorgente: non possiamo sovrascriverci, apriamo la pagina
            webbrowser.open(upd.page)
            self._queue("update_page_opened")
            return {"ok": True}

        def progress(done, total):
            pct = int(done * 100 / total) if total else 0
            if pct >= self._last_pct + 5 or pct == 100:
                self._last_pct = pct
                self._queue("update_progress", pct=pct)

        def work():
            self._last_pct = -1
            try:
                path = updater.download(upd, progress=progress)
            except updater.UpdateError as exc:
                self._queue("update_failed", err=str(exc))
                return
            self._pending_installer = path
            self._queue("update_ready")
            if self._window is not None:
                self._window.destroy()     # main() poi ferma tutto e lancia l'installer
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def start(self, host, name) -> dict:
        with self._lock:
            upd = self._update
        if upd is not None and upd.mandatory:
            self._queue("update_required", version=upd.version)
            return {"ok": False}
        host, name = (host or "").strip(), (name or "").strip()
        if not host:
            self._queue("need_ip")
            return {"ok": False}
        if not name:
            self._queue("need_name")
            return {"ok": False}
        settings.update(host=host, name=name)
        self._spawn(self._engine.start, host, name)
        return {"ok": True}

    def stop(self) -> dict:
        self._spawn(self._engine.stop)
        return {"ok": True}


def main() -> None:
    if not winproc.single_instance():
        winproc.message_box(f"{APP_NAME} è già aperto.", APP_NAME)
        return
    _setup_logging()
    log.info("%s %s avviato", APP_NAME, __version__)

    import webview  # dopo il controllo istanza: avvio piu' rapido se gia' aperto

    api = Api()
    api._window = webview.create_window(APP_NAME, paths.web_index(), js_api=api,
                                       width=560, height=780, min_size=(460, 640),
                                       background_color="#12100c")
    webview.start()
    # finestra chiusa: fermiamo ponte e Mumble prima di uscire
    api._engine.stop(quiet=True)
    if api._pending_installer:
        updater.launch_installer(api._pending_installer)
    log.info("chiuso")


if __name__ == "__main__":
    main()
