"""Microfoni e casse di Windows, con l'ID che Mumble salva nelle impostazioni.

Mumble identifica i dispositivi per ID-endpoint (es. '{0.0.1.00000000}.{guid}'),
non per nome: leggiamo nome e ID dal registro MMDevices (sola lettura).
"""

from __future__ import annotations

from collections import Counter

_NAME_KEY = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"


def _endpoints(flow: str) -> list:
    import winreg
    prefix = "{0.0.1.00000000}" if flow == "Capture" else "{0.0.0.00000000}"
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio" + "\\" + flow
    items = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, guid) as dk:
                        if winreg.QueryValueEx(dk, "DeviceState")[0] != 1:
                            continue  # solo dispositivi attivi
                        with winreg.OpenKey(dk, "Properties") as pk:
                            name = str(winreg.QueryValueEx(pk, _NAME_KEY)[0])
                except OSError:
                    continue
                items.append({"name": name, "id": prefix + "." + guid})
    except OSError:
        return []
    dup = Counter(d["name"] for d in items)
    for d in items:
        d["label"] = d["name"] if dup[d["name"]] == 1 else d["name"] + " (" + d["id"].split(".")[-1][1:9] + ")"
    items.sort(key=lambda d: d["label"].lower())
    return items


def list_audio_devices() -> dict:
    try:
        return {"input": _endpoints("Capture"), "output": _endpoints("Render")}
    except Exception:  # noqa: BLE001
        return {"input": [], "output": []}
