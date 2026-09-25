"""MumbleLink shared-memory writer (Windows).

Implements the ``LinkedMem`` structure used by Mumble's built-in *Link* plugin
for positional audio. Any process may create/open the named shared memory
``MumbleLink`` and write this structure; the Mumble client reads it.

References (see RESEARCH.md):
- https://www.mumble.info/documentation/developer/positional-audio/link-plugin/
- https://github.com/mumble-voip/mumble/blob/master/plugins/link/link.cpp

LinkedMem layout (Windows, wchar_t = 2 bytes), total 5460 bytes:
    UINT32 uiVersion;            // must be 2
    UINT32 uiTick;               // increment every update
    float  fAvatarPosition[3];
    float  fAvatarFront[3];      // unit vector
    float  fAvatarTop[3];        // unit vector, perpendicular to front
    wchar_t name[256];           // game/app name shown by Mumble
    float  fCameraPosition[3];
    float  fCameraFront[3];
    float  fCameraTop[3];
    wchar_t identity[256];       // uniquely identifies the player in the context
    UINT32 context_len;
    unsigned char context[256];  // only players with identical context hear each other
    wchar_t description[2048];

Coordinate system: left-handed, +X right, +Y up, +Z forward, units = meters.
"""

from __future__ import annotations

import mmap
import struct
from typing import Sequence

TAGNAME = "MumbleLink"            # Windows shared-memory object name
LINKEDMEM_SIZE = 5460            # sizeof(LinkedMem) on Windows
LINK_VERSION = 2

# little-endian, standard sizes, no alignment padding (all fields stay 4-aligned).
#   II         uiVersion, uiTick
#   3f 3f 3f   avatar position / front / top
#   512s       name        (256 wchar * 2)
#   3f 3f 3f   camera position / front / top
#   512s       identity    (256 wchar * 2)
#   I          context_len
#   256s       context
#   4096s      description (2048 wchar * 2)
_STRUCT = struct.Struct("<II 3f 3f 3f 512s 3f 3f 3f 512s I 256s 4096s")
assert _STRUCT.size == LINKEDMEM_SIZE, _STRUCT.size

Vec3 = Sequence[float]


def _pack_wchars(text: str, max_chars: int) -> bytes:
    """Encode ``text`` as a null-terminated UTF-16-LE buffer of exactly
    ``max_chars`` wide chars (``max_chars * 2`` bytes)."""
    max_bytes = max_chars * 2
    raw = text.encode("utf-16-le")[: max_bytes - 2]  # leave room for a NUL
    # If the byte cut fell between the two code units of a surrogate pair (any
    # astral-plane char, e.g. an emoji), drop the dangling high surrogate so the
    # buffer never ends with an unpaired 0xD800-0xDBFF unit.
    if len(raw) >= 2:
        last = int.from_bytes(raw[-2:], "little")
        if 0xD800 <= last <= 0xDBFF:
            raw = raw[:-2]
    return raw.ljust(max_bytes, b"\x00")


class MumbleLink:
    """Create/open the ``MumbleLink`` shared memory and write LinkedMem frames."""

    def __init__(self, name: str = "Neverwinter Nights",
                 description: str = "NWN positional audio bridge") -> None:
        self.name = name
        self.description = description
        self._tick = 0
        # fileno=-1 + tagname => pagefile-backed named mapping; created if absent,
        # otherwise the existing one (e.g. created by Mumble) is opened.
        self._mm = mmap.mmap(-1, LINKEDMEM_SIZE, tagname=TAGNAME,
                             access=mmap.ACCESS_WRITE)

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        try:
            self._mm.close()
        except Exception:
            pass

    def __enter__(self) -> "MumbleLink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writing -------------------------------------------------------------
    def update(self,
               avatar_pos: Vec3, avatar_front: Vec3, avatar_top: Vec3,
               camera_pos: Vec3 | None = None,
               camera_front: Vec3 | None = None,
               camera_top: Vec3 | None = None,
               identity: str = "",
               context: bytes = b"") -> None:
        """Write one LinkedMem frame and increment the tick.

        Mumble considers the link active only while ``uiTick`` keeps changing.
        """
        self._tick = (self._tick + 1) & 0xFFFFFFFF
        if camera_pos is None:
            camera_pos = avatar_pos
        if camera_front is None:
            camera_front = avatar_front
        if camera_top is None:
            camera_top = avatar_top

        ctx = bytes(context)[:256]
        ctx_len = len(ctx)
        ctx_field = ctx.ljust(256, b"\x00")

        data = _STRUCT.pack(
            LINK_VERSION, self._tick,
            float(avatar_pos[0]), float(avatar_pos[1]), float(avatar_pos[2]),
            float(avatar_front[0]), float(avatar_front[1]), float(avatar_front[2]),
            float(avatar_top[0]), float(avatar_top[1]), float(avatar_top[2]),
            _pack_wchars(self.name, 256),
            float(camera_pos[0]), float(camera_pos[1]), float(camera_pos[2]),
            float(camera_front[0]), float(camera_front[1]), float(camera_front[2]),
            float(camera_top[0]), float(camera_top[1]), float(camera_top[2]),
            _pack_wchars(identity, 256),
            ctx_len, ctx_field,
            _pack_wchars(self.description, 2048),
        )
        self._mm[0:LINKEDMEM_SIZE] = data

    def deactivate(self) -> None:
        """Zero the version so Mumble stops treating the link as active."""
        self._mm[0:8] = struct.pack("<II", 0, 0)

    @property
    def tick(self) -> int:
        return self._tick


def _unpack_wchars(raw: bytes) -> str:
    """Decode a UTF-16-LE wchar buffer, trimming at the first NUL."""
    s = raw.decode("utf-16-le", "replace")
    nul = s.find("\x00")
    return s[:nul] if nul >= 0 else s


def unpack_frame(data: bytes) -> dict:
    """Decode a raw ``LinkedMem`` buffer into a dict. For tests/debugging."""
    f = _STRUCT.unpack(data[:LINKEDMEM_SIZE])
    return {
        "version": f[0], "tick": f[1],
        "avatar_pos": (f[2], f[3], f[4]),
        "avatar_front": (f[5], f[6], f[7]),
        "avatar_top": (f[8], f[9], f[10]),
        "name": _unpack_wchars(f[11]),
        "camera_pos": (f[12], f[13], f[14]),
        "camera_front": (f[15], f[16], f[17]),
        "camera_top": (f[18], f[19], f[20]),
        "identity": _unpack_wchars(f[21]),
        "context_len": f[22],
        "context": f[23][: f[22]],
        "description": _unpack_wchars(f[24]),
    }


def read_current() -> dict:
    """Open the ``MumbleLink`` shared memory read-only and decode the current
    frame. Returns the dict from :func:`unpack_frame` (version 0 if never written).

    NOTE: with ``fileno=-1`` this CREATES the pagefile-backed mapping if it does
    not already exist, so it cannot distinguish 'never created' (Mumble/bridge not
    running) from 'created but idle' -- both surface as version 0. For diagnostics
    only; not a true read-only existence probe.
    """
    reader = mmap.mmap(-1, LINKEDMEM_SIZE, tagname=TAGNAME, access=mmap.ACCESS_READ)
    try:
        return unpack_frame(reader.read(LINKEDMEM_SIZE))
    finally:
        reader.close()
