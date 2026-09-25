"""Coordinate mapping from Neverwinter Nights world space to Mumble space.

NWN world: position Vector(x, y, z) in meters (1 NWN unit == 1 meter; a tile is
10x10 m). x grows East, y grows North, z is height. Facing is in degrees, CCW
from East (0 deg = +x/East, 90 deg = +y/North).

Mumble: left-handed, +X right, +Y up, +Z forward, units = meters.

What matters for positional audio is that *every* client uses the SAME mapping,
so relative geometry between players is consistent. We map NWN's ground plane to
Mumble's X/Z plane and NWN height to Mumble Y:

    Mumble.X = NWN.x      (East)
    Mumble.Y = NWN.z      (height)
    Mumble.Z = NWN.y      (North -> forward)

The exact handedness can be fine-tuned at runtime; distance attenuation (the
primary effect) is correct regardless because the scale is 1:1 in meters.
"""

from __future__ import annotations

import math
from typing import Tuple

Vec3 = Tuple[float, float, float]

TOP: Vec3 = (0.0, 1.0, 0.0)  # world up


def nwn_pos_to_mumble(x: float, y: float, z: float) -> Vec3:
    """Map an NWN position to Mumble's left-handed meter space."""
    return (x, z, y)


def nwn_facing_to_front(facing_deg: float) -> Vec3:
    """Map an NWN facing (degrees CCW from East) to a Mumble unit front vector
    lying in the horizontal plane."""
    r = math.radians(facing_deg)
    fx = math.cos(r)   # East component  -> Mumble X
    fy = math.sin(r)   # North component -> Mumble Z
    return (fx, 0.0, fy)
