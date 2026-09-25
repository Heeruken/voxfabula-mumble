"""Dove l'app tiene le sue cose.

Tutto sta in UNA cartella per utente:  %APPDATA%\\Vox Fabula Voice\\
  settings.json          nome, server, lingua, microfono/casse
  mumble_settings.json   le impostazioni del NOSTRO Mumble (mai quelle dell'utente)
  mumble.sqlite          il database del nostro Mumble
  voice.log              log dell'ultima sessione (sovrascritto a ogni avvio)
Niente file in Temp, niente scritture accanto all'exe (che da installato sta in
una cartella di programma).
"""

from __future__ import annotations

import os
import shutil
import sys

from . import APP_NAME, OLD_APP_NAME

# file da riportare dalla cartella col vecchio nome dell'app (nome, server, tasto...)
_CARRY_OVER = ("settings.json", "mumble_settings.json", "mumble.sqlite")


def data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        _carry_over(os.path.join(base, OLD_APP_NAME), d)
    return d


def _carry_over(old: str, new: str) -> None:
    """Prima apertura col nome nuovo: copia (non sposta) le impostazioni della
    versione di prova "NWN Voce", cosi' nessuno deve rifarle."""
    for name in _CARRY_OVER:
        src = os.path.join(old, name)
        if os.path.isfile(src):
            try:
                shutil.copy2(src, os.path.join(new, name))
            except OSError:
                pass


def settings_file() -> str:
    return os.path.join(data_dir(), "settings.json")


def mumble_settings_file() -> str:
    return os.path.join(data_dir(), "mumble_settings.json")


def mumble_database_file() -> str:
    return os.path.join(data_dir(), "mumble.sqlite")


def log_file() -> str:
    return os.path.join(data_dir(), "voice.log")


def resources_dir() -> str:
    """Risorse impacchettate: nella build PyInstaller e' la cartella _internal,
    da sorgente e' la radice del progetto."""
    mp = getattr(sys, "_MEIPASS", None)
    if mp:
        return mp
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def web_index() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(here, "web", "index.html"),
              os.path.join(resources_dir(), "web", "index.html")):
        if os.path.exists(c):
            return c
    return os.path.join(here, "web", "index.html")


def mumble_dir() -> str | None:
    """Cartella del Mumble portatile incluso (build: _internal\\mumble,
    sorgente: vendor\\mumble). None se manca."""
    root = resources_dir()
    for c in (os.path.join(root, "mumble"), os.path.join(root, "vendor", "mumble")):
        if os.path.exists(os.path.join(c, "mumble.exe")):
            return c
    return None
