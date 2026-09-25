"""Roster in memoria condivisa (Windows): il bridge scrive QUI chi-e'-dove +
modalita' di portata; il plugin Mumble (plugin/vc_range.c) lo legge nel thread
audio per attenuare ogni voce in base a distanza e sussurra/parla/urla.

Perche' memoria condivisa e non il DB: il callback audio di Mumble dev'essere
velocissimo (niente file/SQL). Una mappa di memoria e' una lettura immediata.

Layout BINARIO fisso (little-endian) -- DEVE combaciare con vc_range.c:
    header:  uint32 magic, uint32 seq, uint32 count, uint32 reserved   (16 B)
    entry[]: char name[32], char area[32], float x,y,z, int32 mode      (80 B)
mode: 0=sussurra, 1=parla, 2=urla.

MAX_ENTRIES=64 implica un tetto di ~32 giocatori: il provider sqlfile emette
fino a 2 entry per giocatore (nome account + nome personaggio quando differiscono),
quindi 64 slot coprono ~32 giocatori. Oltre, write() tronca silenziosamente.
"""

from __future__ import annotations

import mmap
import struct
from typing import Iterable, Tuple

TAG = "nwn_voice_roster"          # nome dell'oggetto di memoria condivisa (sessione utente)
MAGIC = 0x52435601                # 'VCR\x01' -- il plugin verifica questo
MAX_ENTRIES = 64

_HEADER = struct.Struct("<IIII")          # magic, seq, count, reserved
_ENTRY = struct.Struct("<32s32sfffi")     # name, area, x, y, z, mode
SIZE = _HEADER.size + MAX_ENTRIES * _ENTRY.size

# (name, area, x, y, z, mode)
RosterEntry = Tuple[str, str, float, float, float, int]


class RosterWriter:
    """Crea/apre la memoria condivisa e ci scrive il roster. Tienila viva: quando
    il processo termina, i dati smettono di aggiornarsi (il plugin torna a non
    attenuare)."""

    def __init__(self) -> None:
        self._mm = mmap.mmap(-1, SIZE, tagname=TAG)
        self._seq = 0
        self._warned_overflow = False

    def write(self, entries: Iterable[RosterEntry]) -> None:
        all_items = list(entries)
        items = all_items[:MAX_ENTRIES]
        if len(all_items) > MAX_ENTRIES and not self._warned_overflow:
            # Una sola volta: oltre MAX_ENTRIES qualcuno viene scartato e udito a
            # volume pieno (non attenuato). Vedi tetto ~32 giocatori nel docstring.
            self._warned_overflow = True
            print(f"[roster] {len(all_items)} entry > MAX_ENTRIES={MAX_ENTRIES}: "
                  "le eccedenti vengono scartate", flush=True)
        # SEQLOCK: header con seq DISPARI (scrittura in corso) -> scrivo le entry ->
        # header con seq PARI (dati stabili). Il lettore (vc_range.c) rilegge il seq:
        # se e' dispari o e' cambiato durante la lettura, riprova -> niente letture
        # "strappate" mentre stiamo riscrivendo le entry in-place. (audit P2)
        self._seq = ((self._seq + 1) | 1) & 0xFFFFFFFF        # dispari = scrittura in corso
        self._mm[0:_HEADER.size] = _HEADER.pack(MAGIC, self._seq, len(items), 0)
        off = _HEADER.size
        for (name, area, x, y, z, mode) in items:
            self._mm[off:off + _ENTRY.size] = _ENTRY.pack(
                name.encode("utf-8")[:31],
                area.encode("utf-8")[:31],
                float(x), float(y), float(z), int(mode),
            )
            off += _ENTRY.size
        self._seq = (self._seq + 1) & 0xFFFFFFFF              # pari = dati stabili
        self._mm[0:_HEADER.size] = _HEADER.pack(MAGIC, self._seq, len(items), 0)

    def close(self) -> None:
        try:
            self._mm.close()
        except Exception:
            pass
