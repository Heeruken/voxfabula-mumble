"""Le impostazioni del NOSTRO Mumble.

L'app lancia il Mumble portatile incluso con:
    mumble.exe -m -c "%APPDATA%\\Vox Fabula Voice\\mumble_settings.json"
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



def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


PTT_SHORTCUT_INDEX = 1          # indice della funzione "Push-to-Talk" nelle scorciatoie di Mumble
_EMPTY_DATA = "AAAAAAE="        # QVariant nullo: come lo scrive Mumble per il push-to-talk


def write_config(mumble_dir: str, *, input_device: Optional[str] = None,
                 output_device: Optional[str] = None,
                 transmit: str = "ptt", ptt_key: Optional[dict] = None) -> str:
    """Crea/aggiorna il file di impostazioni del nostro Mumble e ne ritorna il percorso.

    transmit: "ptt" (premi per parlare, predefinito) o "vad" (attivazione vocale).
    ptt_key:  tasto del push-to-talk (vedi mumble_keys); None = Blocco Maiuscole.
    """
    from . import mumble_keys
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

    # Teniamo accese solo le NOSTRE copie di vc_range/link. (Con -c Mumble cerca i
    # plugin utente accanto al file di configurazione, non in %APPDATA%\Mumble:
    # eventuali copie vecchie li' non vengono nemmeno lette.)
    keep = {plugin_key(link_p), plugin_key(vc_p)}
    for pkey, entry in list(plugins.items()):
        if not isinstance(entry, dict):
            del plugins[pkey]
            continue
        # OGNI voce deve avere TUTTI i campi: Mumble li legge con .at() e, se ne
        # manca uno, lancia un'eccezione e si pianta all'avvio (crash reale del
        # 25/09: una voce senza 'positional_data_enabled').
        entry.setdefault("enabled", False)
        entry.setdefault("positional_data_enabled", False)
        entry.setdefault("keyboard_monitoring_allowed", False)
        entry.setdefault("path", "")
        if pkey in keep:
            continue
        ep = str(entry["path"]).replace("\\", "/").lower()
        if ep.endswith("/vc_range.dll") or ep.endswith("/link.dll"):
            entry["enabled"] = False

    audio = data.setdefault("audio", {})
    audio["mute"] = False
    audio["deaf"] = False

    # Come si trasmette: il giocatore lo sceglie nell'app, mai dentro Mumble.
    key = ptt_key if ptt_key and mumble_keys.is_supported(ptt_key) else mumble_keys.DEFAULT_KEY
    button, suppress = mumble_keys.encode(key)
    audio["transmit_mode"] = "VAD" if transmit == "vad" else "PTT"
    shortcuts = data.setdefault("shortcuts", {})
    defined = [s for s in shortcuts.get("defined", [])
               if isinstance(s, dict) and s.get("index") != PTT_SHORTCUT_INDEX]
    defined.append({"buttons": [button], "data": _EMPTY_DATA,
                    "index": PTT_SHORTCUT_INDEX, "suppress": suppress})
    shortcuts["defined"] = defined
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
    for k in ("check_for_updates", "check_for_plugin_updates", "auto_update_plugins"):
        misc.pop(k, None)                      # stavano qui per errore: Mumble le ignorava
    data.pop("overlay", None)                  # sezione che non esiste: Mumble la ignorava

    # Niente "New version available" di Mumble: gli aggiornamenti li gestisce l'app.
    # La sezione giusta e' "update" (verificato: Mumble la rilegge e la risalva).
    update = data.setdefault("update", {})
    update["check_for_updates"] = False
    update["check_for_plugin_updates"] = False
    update["auto_update_plugins"] = False

    ui = data.setdefault("ui", {})
    ui["quit_behavior"] = "AlwaysQuit"         # alla chiusura esce e salva, senza domande

    data["mumble_has_quit_normally"] = True    # niente avvisi "chiusura anomala"
    data.setdefault("settings_version", 1)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4)
    os.replace(tmp, path)
    log.info("configurazione Mumble scritta: %s", path)
    return path


# ---------------------------------------------------------------------------
# Certificato del server Mumble di VoxFabula.
#
# Il server usa un certificato auto-firmato: al primo collegamento Mumble
# chiederebbe "Vuoi comunque accettare questo certificato?". Mumble ricorda i
# certificati accettati nella tabella `cert` del suo database (host, porta,
# impronta SHA-1 in esadecimale minuscolo): la pre-riempiamo con l'impronta del
# server, cosi' la domanda non compare.
#
# A ogni Connetti l'app chiede al server il certificato che presenta ADESSO
# (fetch_server_digest) e registra quello: se il certificato cambia (container
# di Mumble ricreato...) l'app si adegua da sola, senza nuove versioni. E' cio'
# che farebbe il giocatore cliccando "Si'", solo senza chiederglielo.
# Se il server non risponde, ripieghiamo sull'impronta nota qui sotto (quella
# che Mumble mostra come "Digest certificato server (SHA-1)", senza i due punti).
# ---------------------------------------------------------------------------
SERVER_CERT_SHA1 = "64d902a09386aa3b7c013961ad7a747beed0fde5"

_CERT_TABLE = ("CREATE TABLE IF NOT EXISTS `cert` (`id` INTEGER PRIMARY KEY AUTOINCREMENT, "
               "`hostname` TEXT, `port` INTEGER, `digest` TEXT)")   # schema identico a Mumble 1.5


def fetch_server_digest(host: str, port: int = 64738, timeout: float = 5.0) -> str | None:
    """Impronta SHA-1 (esadecimale minuscolo, come la salva Mumble) del certificato
    che il server Mumble presenta ora. None se non risponde."""
    import socket
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE        # e' auto-firmato: lo leggiamo, non lo verifichiamo
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ValueError) as exc:
        log.info("certificato del server non letto (%s:%s): %s", host, port, exc)
        return None
    return hashlib.sha1(der).hexdigest() if der else None


def seed_server_cert(host: str, port: int = 64738, digest: str = SERVER_CERT_SHA1,
                     replace: bool = False) -> bool:
    """Fa riconoscere a Mumble il certificato del server per ``host:port``.
    replace=False: non tocca una voce gia' presente per lo stesso host.
    replace=True: la sostituisce (impronta appena letta dal server).
    True se ha scritto qualcosa."""
    import sqlite3
    db = paths.mumble_database_file()
    if not os.path.exists(db):
        open(db, "ab").close()
    con = sqlite3.connect(db, timeout=5)
    try:
        con.execute(_CERT_TABLE)
        rows = con.execute("SELECT digest FROM cert WHERE hostname=? AND port=?", (host, port)).fetchall()
        if rows and (not replace or rows == [(digest,)]):
            return False
        con.execute("DELETE FROM cert WHERE hostname=? AND port=?", (host, port))
        con.execute("INSERT INTO cert (hostname, port, digest) VALUES (?,?,?)", (host, port, digest))
        con.commit()
        log.info("certificato del server %s per %s:%s (%s)",
                 "aggiornato" if rows else "pre-accettato", host, port, digest)
        return True
    finally:
        con.close()


def accept_server_cert(host: str, port: int = 64738) -> str:
    """Quello che fa Connetti: impronta letta dal server se risponde, altrimenti
    quella nota. Ritorna "live" o "known"."""
    digest = fetch_server_digest(host, port)
    if digest:
        seed_server_cert(host, port, digest, replace=True)
        return "live"
    seed_server_cert(host, port)
    return "known"
