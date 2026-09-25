"""Le impostazioni del NOSTRO Mumble.

L'app lancia il Mumble portatile incluso con:
    mumble.exe -m -c "%APPDATA%\\NWN Voce\\mumble_settings.json"
  -c  = usa questo file di impostazioni invece di quello globale dell'utente
  -m  = puo' girare accanto a un altro Mumble gia' aperto
Quindi il Mumble che l'utente usa per altro NON viene ne' chiuso ne' toccato.
Mumble chiede anche un database separato quando si usa -c: e' database_location.

Qui aggiorniamo solo le chiavi che ci servono, lasciando tutto il resto (per
esempio il certificato che Mumble genera da solo al primo avvio e salva qui).
Tutte le chiavi sono state verificate nel mumble.exe 1.5.12 incluso.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Optional

from . import paths

log = logging.getLogger(__name__)


def _fwd(path: str) -> str:
    return path.replace("\\", "/")


def plugin_key(dll_path: str) -> str:
    """Mumble indicizza ogni plugin con lo SHA-1 del suo percorso (barre in avanti)."""
    return hashlib.sha1(_fwd(dll_path).encode("utf-8")).hexdigest()


def _user_plugin_dir() -> str:
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    return os.path.join(base, "Mumble", "Mumble", "Plugins")


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_config(mumble_dir: str, *, input_device: Optional[str] = None,
                 output_device: Optional[str] = None) -> str:
    """Crea/aggiorna il file di impostazioni del nostro Mumble e ne ritorna il percorso."""
    path = paths.mumble_settings_file()
    data = _load(path)

    # Audio posizionale NATIVO di Mumble spento: ronza (issue Mumble #4169).
    # Distanza, portata e pan stereo li fa tutti il plugin vc_range.
    pa = data.setdefault("positional_audio", {})
    pa["enable_positional_audio"] = False
    pa["transmit_position"] = False

    plugins_dir = os.path.join(mumble_dir, "plugins")
    link_p = _fwd(os.path.join(plugins_dir, "link.dll"))
    vc_p = _fwd(os.path.join(plugins_dir, "vc_range.dll"))
    plugins = data.setdefault("plugins", {})
    link = plugins.setdefault(plugin_key(link_p), {})
    link.update(enabled=True, positional_data_enabled=True, path=link_p)
    link.setdefault("keyboard_monitoring_allowed", False)
    vc = plugins.setdefault(plugin_key(vc_p), {})
    vc.update(enabled=True, path=vc_p)
    vc.setdefault("positional_data_enabled", False)
    vc.setdefault("keyboard_monitoring_allowed", False)

    # Una vecchia versione dell'app copiava vc_range.dll anche nella cartella
    # plugin dell'utente: Mumble la caricherebbe DUE volte (voce attenuata due
    # volte). Teniamo accese solo le nostre copie.
    keep = {plugin_key(link_p), plugin_key(vc_p)}
    extra = []
    udir = _user_plugin_dir()
    for fn in ("vc_range.dll", "link.dll"):
        p = os.path.join(udir, fn)
        if os.path.exists(p):
            extra.append(_fwd(p))
    for p in extra:
        entry = plugins.setdefault(plugin_key(p), {"path": p})
        entry["enabled"] = False
    for pkey, entry in plugins.items():
        if pkey in keep or not isinstance(entry, dict):
            continue
        ep = str(entry.get("path", "")).replace("\\", "/").lower()
        if ep.endswith("/vc_range.dll") or ep.endswith("/link.dll"):
            entry["enabled"] = False

    audio = data.setdefault("audio", {})
    audio["mute"] = False
    audio["deaf"] = False
    backend = data.setdefault("audio_backend", {})
    if input_device or output_device:
        audio["input_system"] = "WASAPI"
        audio["output_system"] = "WASAPI"
    # Vuoto = "Predefinito di Windows": togliamo una scelta precedente.
    for key, val in (("wasapi_input", input_device), ("wasapi_output", output_device)):
        if val:
            backend[key] = val
        else:
            backend.pop(key, None)

    # Mumble NON crea il database in una posizione scelta da noi: se il file manca
    # mostra "Database: File not found" e resta bloccato su quell'avviso.
    # Un file vuoto e' un database SQLite valido: Mumble ci crea le sue tabelle.
    db = paths.mumble_database_file()
    if not os.path.exists(db):
        open(db, "ab").close()
    misc = data.setdefault("misc", {})
    misc["database_location"] = _fwd(db)
    misc["audio_wizard_has_been_shown"] = True
    misc["viewed_server_ping_consent_message"] = True
    misc["check_for_updates"] = False          # nessuna chiamata a casa di Mumble
    misc["check_for_plugin_updates"] = False
    misc["auto_update_plugins"] = False

    ui = data.setdefault("ui", {})
    ui["quit_behavior"] = "AlwaysQuit"         # alla chiusura esce e salva, senza domande

    # Overlay nei giochi: non lo usiamo e i suoi eseguibili non sono nel pacchetto.
    data.setdefault("overlay", {})["enable_overlay"] = False

    data["mumble_has_quit_normally"] = True    # niente avvisi "chiusura anomala"
    data.setdefault("settings_version", 1)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4)
    os.replace(tmp, path)
    log.info("configurazione Mumble scritta: %s", path)
    return path
