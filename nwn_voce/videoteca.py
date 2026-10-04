"""La videoteca: scarica (e tiene aggiornati) i video del server.

All'avvio della sessione legge il catalogo pubblicato accanto ai file di NWSync:
    https://nwsync.voxfabula.it/prod/2985642d4b434caab09571e1ec2058f8/cinema/catalogo.json
    {"video": [{"nome": "ingresso_ossario", "file": "ingresso_ossario.vfv",
                "sha256": "<impronta>", "size": 12345678}, ...]}
e scarica nella cartella video (%APPDATA%\\...\\video) quelli che mancano o sono
cambiati. Cosi' quando il server chiede un video e' gia' sul PC: niente attese.

Regole (un programma che scarica file e' proprio quello che gli antivirus guardano):
  - solo HTTPS e solo il dominio del server, controllato anche dopo i redirect;
  - ogni file e' verificato con la sua impronta SHA-256; se non torna, si butta;
  - si cancellano solo i video che abbiamo scaricato NOI e che il server ha tolto;
  - ogni scaricamento finisce nel registro attivita'.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urlsplit

from . import APP_NAME, __version__
from .regia import ESTENSIONI, NOME_RE, cartella_video

log = logging.getLogger(__name__)

# dentro la cartella di NWSync: Cloudflare serve SOLO quella (altrove 403). Lo script di
# pubblicazione NWSync cancella solo i file spariti dai suoi repo locali: cinema/ non lo tocca.
BASE = "https://nwsync.voxfabula.it/prod/2985642d4b434caab09571e1ec2058f8/cinema/"
CATALOGO = BASE + "catalogo.json"
HOSTS = frozenset({"nwsync.voxfabula.it"})
MAX_FILE = 600 * 1024 * 1024       # un video piu' grosso e' sicuramente un errore
TIMEOUT = 20
_LOCALE = "catalogo_locale.json"   # cosa abbiamo scaricato noi: {file: sha256}
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class _SoloNostri(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _sicuro(newurl):
            raise urllib.error.URLError(f"redirect verso un indirizzo non ammesso: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _sicuro(url: str) -> bool:
    u = urlsplit(url)
    return u.scheme == "https" and (u.hostname or "").lower() in HOSTS


def _apri(url: str):
    if not _sicuro(url):
        raise ValueError(f"indirizzo non ammesso: {url}")
    req = urllib.request.Request(url, headers={
        "User-Agent": f"{APP_NAME.replace(' ', '-')}/{__version__}"})
    return urllib.request.build_opener(_SoloNostri).open(req, timeout=TIMEOUT)


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blocco in iter(lambda: fh.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def _voci_valide(dati) -> list:
    """Le voci del catalogo, scartando tutto quello che non e' come ce lo aspettiamo."""
    out = []
    for v in (dati or {}).get("video", []) if isinstance(dati, dict) else []:
        try:
            nome, file = str(v["nome"]), str(v["file"])
            sha, size = str(v["sha256"]).lower(), int(v["size"])
        except (KeyError, TypeError, ValueError):
            continue
        base, est = os.path.splitext(file)
        if NOME_RE.match(nome) and base == nome and est in ESTENSIONI \
                and _SHA_RE.match(sha) and 0 < size <= MAX_FILE:
            out.append({"nome": nome, "file": file, "sha256": sha, "size": size})
    return out


def _leggi_locale(cartella: str) -> dict:
    try:
        with open(os.path.join(cartella, _LOCALE), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _scrivi_locale(cartella: str, d: dict) -> None:
    p = os.path.join(cartella, _LOCALE)
    try:
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)
        os.replace(p + ".tmp", p)
    except OSError:
        pass


def _scarica(voce: dict, cartella: str) -> bool:
    dest = os.path.join(cartella, voce["file"])
    parte = dest + ".part"
    h = hashlib.sha256()
    letti = 0
    try:
        with _apri(BASE + voce["file"]) as r, open(parte, "wb") as fh:
            while True:
                blocco = r.read(1 << 20)
                if not blocco:
                    break
                letti += len(blocco)
                if letti > voce["size"]:
                    raise ValueError("file piu' grande del previsto")
                h.update(blocco)
                fh.write(blocco)
        if letti != voce["size"] or h.hexdigest() != voce["sha256"]:
            raise ValueError("impronta o dimensione sbagliata")
        os.replace(parte, dest)
        return True
    except (OSError, ValueError) as exc:
        log.warning("video %s non scaricato: %s", voce["file"], exc)
        try:
            os.remove(parte)
        except OSError:
            pass
        return False


def aggiorna(registro, catalogo: Optional[dict] = None) -> int:
    """Allinea la cartella video al catalogo del server. Ritorna quanti video ha
    scaricato. ``catalogo`` gia' letto: solo per i test."""
    cartella = cartella_video()
    if catalogo is None:
        try:
            with _apri(CATALOGO) as r:
                catalogo = json.loads(r.read(1 << 20).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 -- niente catalogo = niente da fare
            log.info("catalogo video non disponibile: %s", exc)
            return 0
    if not isinstance(catalogo, dict) or not isinstance(catalogo.get("video"), list):
        log.warning("catalogo video malformato: ignorato")
        return 0                                       # mai cancellare per un catalogo rotto
    voci = _voci_valide(catalogo)
    locale = _leggi_locale(cartella)
    scaricati = 0
    for v in voci:
        dest = os.path.join(cartella, v["file"])
        if locale.get(v["file"]) == v["sha256"] and os.path.isfile(dest) \
                and os.path.getsize(dest) == v["size"]:
            continue                                   # gia' a posto
        if os.path.isfile(dest) and os.path.getsize(dest) == v["size"] \
                and _sha_file(dest) == v["sha256"]:
            locale[v["file"]] = v["sha256"]            # c'era gia' (copiato a mano)
            continue
        if _scarica(v, cartella):
            locale[v["file"]] = v["sha256"]
            scaricati += 1
            registro.scrivi("video_scaricato", video=v["nome"],
                            mb=round(v["size"] / 1048576, 1))
    # via i video scaricati da noi che il server non pubblica piu'
    nel_catalogo = {v["file"] for v in voci}
    for file in [f for f in locale if f not in nel_catalogo]:
        try:
            os.remove(os.path.join(cartella, os.path.basename(file)))
            registro.scrivi("video_tolto", video=os.path.splitext(file)[0])
        except OSError:
            pass
        locale.pop(file, None)
    _scrivi_locale(cartella, locale)
    return scaricati
