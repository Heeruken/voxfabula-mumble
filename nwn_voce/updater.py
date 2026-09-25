"""Aggiornamento automatico dalle Release di GitHub (Heeruken/voxfabula-mumble).

All'avvio l'app chiede a GitHub qual e' l'ultima versione pubblicata. Se e' piu'
nuova, CHIEDE all'utente; se accetta:
  1. scarica l'installer della Release (NWN-Voce-Setup-<ver>.exe),
  2. ne controlla l'impronta SHA-256 con quella calcolata da GitHub ("digest"),
  3. l'app si chiude e lancia l'installer in modalita' silenziosa, che
     sostituisce i file e la riapre gia' aggiornata.

Regole di sicurezza (un programma che scarica ed esegue un altro programma e'
proprio quello che gli antivirus sorvegliano):
  - solo HTTPS e solo domini di GitHub, controllati anche DOPO i redirect;
  - una Release senza impronta viene ignorata; impronta sbagliata = file buttato;
  - mai senza il consenso dell'utente.

Versione minima: se nelle note della Release c'e' una riga
    Versione minima: 1.2.0
le app piu' vecchie devono aggiornarsi prima di collegarsi (serve quando cambia
il protocollo col relay).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

from . import APP_NAME, __version__, paths

log = logging.getLogger(__name__)

REPO = "Heeruken/voxfabula-mumble"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ALLOWED_HOSTS = frozenset({
    "api.github.com",
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
})
ASSET_RE = re.compile(r"^NWN-Voce-Setup-[\d.]+\.exe$", re.IGNORECASE)
MIN_RE = re.compile(r"(?im)^[ \t]*versione[ \t]+minima[ \t]*:[ \t]*v?([\d.]+)[ \t]*$")
TIMEOUT = 15


class UpdateError(Exception):
    """Qualcosa e' andato storto: il messaggio e' pensato per l'utente."""


@dataclass
class Update:
    version: str
    notes: str
    url: str
    sha256: str
    size: int
    mandatory: bool
    page: str


def parse_version(v: str) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", v or "")][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def _check_url(url: str, allowed_hosts: Iterable[str], schemes: Iterable[str]) -> None:
    parts = urlsplit(url)
    if parts.scheme not in schemes or (parts.hostname or "") not in allowed_hosts:
        raise UpdateError(f"indirizzo non consentito: {parts.scheme}://{parts.hostname}")


def _open(url: str, allowed_hosts, schemes, accept: str):
    _check_url(url, allowed_hosts, schemes)
    req = urllib.request.Request(url, headers={
        "User-Agent": f"{APP_NAME.replace(' ', '-')}/{__version__}",
        "Accept": accept,
    })
    resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    try:
        _check_url(resp.geturl(), allowed_hosts, schemes)   # anche dopo i redirect
    except UpdateError:
        resp.close()
        raise
    return resp


def check(current: str = __version__, *, api_url: str = API_LATEST,
          allowed_hosts: Iterable[str] = ALLOWED_HOSTS,
          schemes: Iterable[str] = ("https",)) -> Optional[Update]:
    """L'aggiornamento disponibile, oppure None (nessuno, o GitHub non raggiungibile)."""
    try:
        with _open(api_url, allowed_hosts, schemes, "application/vnd.github+json") as r:
            rel = json.load(r)
    except Exception as exc:  # noqa: BLE001 -- offline, limite GitHub, ecc.: si riprova al prossimo avvio
        log.info("controllo aggiornamenti non riuscito: %s", exc)
        return None

    if rel.get("draft") or rel.get("prerelease"):
        return None
    version = str(rel.get("tag_name", "")).lstrip("vV")
    if not version or not is_newer(version, current):
        return None

    asset = next((a for a in rel.get("assets", []) if ASSET_RE.match(a.get("name", ""))), None)
    if asset is None:
        log.warning("release %s senza installer: ignorata", version)
        return None
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:"):
        log.warning("release %s senza impronta SHA-256: ignorata", version)
        return None

    body = str(rel.get("body") or "")
    m = MIN_RE.search(body)
    mandatory = bool(m) and parse_version(current) < parse_version(m.group(1))
    notes = MIN_RE.sub("", body).strip()[:1500]
    return Update(version=version, notes=notes, url=asset["browser_download_url"],
                  sha256=digest.split(":", 1)[1].lower(), size=int(asset.get("size") or 0),
                  mandatory=mandatory, page=str(rel.get("html_url") or RELEASES_PAGE))


def download(update: Update, dest_dir: Optional[str] = None, *,
             progress: Optional[Callable[[int, int], None]] = None,
             allowed_hosts: Iterable[str] = ALLOWED_HOSTS,
             schemes: Iterable[str] = ("https",)) -> str:
    """Scarica l'installer e ne verifica l'impronta. Ritorna il percorso."""
    dest_dir = dest_dir or os.path.join(paths.data_dir(), "aggiornamenti")
    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.basename(urlsplit(update.url).path)
    if not ASSET_RE.match(name):
        raise UpdateError(f"nome file inatteso: {name}")
    final = os.path.join(dest_dir, name)
    part = final + ".part"
    h = hashlib.sha256()
    done = 0
    try:
        with _open(update.url, allowed_hosts, schemes, "application/octet-stream") as r, \
                open(part, "wb") as out:
            total = int(r.headers.get("Content-Length") or update.size or 0)
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if h.hexdigest() != update.sha256:
            raise UpdateError("il file scaricato non corrisponde all'impronta pubblicata: scartato")
        os.replace(part, final)
    except UpdateError:
        _remove(part)
        raise
    except Exception as exc:  # noqa: BLE001
        _remove(part)
        raise UpdateError(f"download non riuscito: {exc}") from exc
    log.info("aggiornamento %s scaricato e verificato: %s", update.version, final)
    return final


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def is_installed() -> bool:
    """True se giriamo dalla cartella dell'installer (non dallo zip o da sorgente):
    solo allora l'app si aggiorna da sola."""
    if not getattr(sys, "frozen", False):
        return False
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", APP_NAME)
    exe_dir = os.path.dirname(sys.executable)
    return os.path.normcase(os.path.abspath(exe_dir)) == os.path.normcase(os.path.abspath(base))


def launch_installer(path: str) -> None:
    """Avvia l'installer in modalita' silenziosa: sostituisce i file e riapre l'app."""
    log.info("avvio l'installer %s", path)
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     close_fds=True)
