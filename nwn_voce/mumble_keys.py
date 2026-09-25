"""Tasti per il push-to-talk, nel formato con cui Mumble li salva.

Mumble 1.5 salva ogni tasto di una scorciatoia come un QVariant serializzato
(QDataStream) e poi codificato in base64:

    00 00 04 00                  tipo: QMetaType::User (1024)
    00                           non nullo
    00 00 00 <n>  <nome> 00      nome del tipo utente: "InputKeyboard" / "InputMouse"
    mouse:    <uint32>           numero del tasto                      (4 byte)
    tastiera: 00 00 <scan code>  scan code fisico, set 1               (3 byte!)

Verificato su due scorciatoie create da Mumble stesso:
    Blocco Maiuscole -> InputKeyboard + 00 00 3A
    tasto del mouse  -> InputMouse    + 00 00 00 05
Per la tastiera non sappiamo come Mumble divida i 3 byte (flag + codice o
viceversa), ma per i tasti "normali" (codice < 0x100, senza prefisso E0: niente
frecce, Ctrl/Alt di destra, Ins/Canc/Home...) il risultato e' comunque
00 00 <codice>: per questo supportiamo solo quelli. Un valore sbagliato in
configurazione puo' far piantare Mumble.
"""

from __future__ import annotations

import base64
import struct

# KeyboardEvent.code (posizione fisica, uguale su ogni layout) -> scan code set 1
_ROWS = {
    0x02: ["Digit1", "Digit2", "Digit3", "Digit4", "Digit5", "Digit6", "Digit7", "Digit8",
           "Digit9", "Digit0", "Minus", "Equal", "Backspace", "Tab"],
    0x10: ["KeyQ", "KeyW", "KeyE", "KeyR", "KeyT", "KeyY", "KeyU", "KeyI", "KeyO", "KeyP",
           "BracketLeft", "BracketRight", "Enter", "ControlLeft"],
    0x1E: ["KeyA", "KeyS", "KeyD", "KeyF", "KeyG", "KeyH", "KeyJ", "KeyK", "KeyL",
           "Semicolon", "Quote", "Backquote", "ShiftLeft", "Backslash"],
    0x2C: ["KeyZ", "KeyX", "KeyC", "KeyV", "KeyB", "KeyN", "KeyM", "Comma", "Period",
           "Slash", "ShiftRight", "NumpadMultiply", "AltLeft", "Space", "CapsLock"],
    0x3B: ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "NumLock",
           "ScrollLock", "Numpad7", "Numpad8", "Numpad9", "NumpadSubtract", "Numpad4",
           "Numpad5", "Numpad6", "NumpadAdd", "Numpad1", "Numpad2", "Numpad3", "Numpad0",
           "NumpadDecimal"],
}
KEYBOARD_SCANCODES = {code: start + i for start, codes in _ROWS.items() for i, code in enumerate(codes)}
KEYBOARD_SCANCODES.update({"IntlBackslash": 0x56, "F11": 0x57, "F12": 0x58})

# MouseEvent.button -> numero del tasto per Mumble (tasti laterali "indietro" / "avanti")
MOUSE_BUTTONS = {3: 4, 4: 5}

DEFAULT_KEY = {"kind": "keyboard", "code": "CapsLock"}
# tasti da "mangiare" (Mumble non li passa agli altri programmi): solo quelli che,
# lasciati passare, darebbero fastidio. Mai le lettere: non si potrebbero piu' scrivere.
SUPPRESS = {"CapsLock", "ScrollLock", "NumLock"}

_NUL = bytes([0])


def _variant(type_name: str, payload: bytes) -> str:
    name = type_name.encode("ascii") + _NUL
    raw = struct.pack(">IB", 1024, 0) + struct.pack(">I", len(name)) + name + payload
    return base64.b64encode(raw).decode("ascii")


def is_supported(key: dict) -> bool:
    try:
        if key.get("kind") == "keyboard":
            return key.get("code") in KEYBOARD_SCANCODES
        if key.get("kind") == "mouse":
            return int(key.get("button")) in MOUSE_BUTTONS
    except (AttributeError, TypeError, ValueError):
        pass
    return False


def encode(key: dict) -> tuple[str, bool]:
    """(pulsante per Mumble in base64, suppress). ValueError se non supportato."""
    if not is_supported(key):
        raise ValueError(f"tasto non supportato: {key!r}")
    if key["kind"] == "keyboard":
        payload = bytes([0, 0, KEYBOARD_SCANCODES[key["code"]]])
        return _variant("InputKeyboard", payload), key["code"] in SUPPRESS
    return _variant("InputMouse", struct.pack(">I", MOUSE_BUTTONS[int(key["button"])])), False
