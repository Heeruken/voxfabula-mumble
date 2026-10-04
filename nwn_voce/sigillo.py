"""Il sigillo dei video: file .vfv che un doppio clic non apre.

Scopo: niente SPOILER. Chi curiosa nella cartella dell'app (o scarica da R2) trova solo
file illeggibili; il Companion li apre quando il server chiede di mostrarli. Non e' una
protezione contro chi smonta il programma (la chiave e' qui dentro): e' stato scelto cosi'
apposta, il video tanto passa sullo schermo.

Formato .vfv:  b"VFV1" | 1 byte = lunghezza dell'estensione | estensione (".webm") |
               16 byte nonce | dati XOR keystream
keystream = SHAKE-256(chiave | nonce), lungo quanto i dati (hashlib, nessuna libreria in piu').
L'integrita' la garantisce l'impronta SHA-256 del file cifrato nel catalogo.
Gli stessi file li prepara voxfabula/docs/cinema/catalogo.py, importando QUESTO modulo.
"""

from __future__ import annotations

import hashlib
import os

MAGIA = b"VFV1"
ESTENSIONE = ".vfv"
_CHIAVE = hashlib.sha256(b"Vox Fabula - il sipario resta chiuso fino all'evento").digest()


def _flusso(nonce: bytes, n: int) -> bytes:
    return hashlib.shake_256(_CHIAVE + nonce).digest(n)


def _xor(a: bytes, b: bytes) -> bytes:
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(len(a), "little")


def sigilla(dati: bytes, est: str) -> bytes:
    """``dati`` = il video, ``est`` = la sua estensione (".webm" / ".mp4")."""
    e = est.lower().encode("ascii")
    if not 1 < len(e) < 16 or not e.startswith(b"."):
        raise ValueError("estensione non valida")
    nonce = os.urandom(16)
    return MAGIA + bytes([len(e)]) + e + nonce + _xor(dati, _flusso(nonce, len(dati)))


def apri(blob: bytes) -> tuple[bytes, str]:
    """Il contrario: (dati del video, estensione). ValueError se non e' un .vfv."""
    if blob[:4] != MAGIA or len(blob) < 6:
        raise ValueError("non e' un video sigillato")
    n = blob[4]
    est = blob[5:5 + n].decode("ascii", "replace")
    if not est.startswith(".") or not 1 < n < 16:
        raise ValueError("intestazione rovinata")
    nonce = blob[5 + n:21 + n]
    corpo = blob[21 + n:]
    if len(nonce) != 16:
        raise ValueError("file troncato")
    return _xor(corpo, _flusso(nonce, len(corpo))), est
