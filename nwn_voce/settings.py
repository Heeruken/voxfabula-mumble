"""Impostazioni dell'app (%APPDATA%\\Vox Fabula Voice\\settings.json)."""

from __future__ import annotations

import json
import os

from . import paths


def load() -> dict:
    try:
        with open(paths.settings_file(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d: dict) -> None:
    path = paths.settings_file()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def update(**kv) -> dict:
    d = load()
    d.update(kv)
    save(d)
    return d
