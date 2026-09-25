"""Tiny line-delimited JSON protocol shared by the relay and this client.

One JSON object per line (``\\n`` terminated), UTF-8. Messages:

client -> relay (once, on connect):
    {"hello": "<player>", "token": "<optional shared secret>"}
client -> relay (on mic change, optional): drives the in-game 'who's talking'
    {"talk": 1}   # or 0 when the mic goes quiet

relay -> client (heartbeated at the relay rate):
    {"v":1,"x":..,"y":..,"z":..,"facing":..,"area":"..","server":"..",
     "name":"..","identity":".."}      # the client's own state
    {"idle": true}                      # connected but no position yet / player absent
    {"error": "<reason>"}               # rejected (bad token, no player)
relay -> client (throttled, full roster for the per-speaker range plugin):
    {"roster": [[name, area, x, y, z, mode], ...]}
        # one entry per player; ``name`` is the account/character display name,
        # matched (case-insensitively) to the Mumble username.

The relay is authoritative for ``server`` and ``area`` so that the Mumble audio
*context* is identical for every client of the same NWN server.
"""

from __future__ import annotations

import json

from .state import PlayerState

PROTO_VERSION = 1

_IDLE = b'{"idle":true}\n'


def encode_hello(player: str, token: str | None = None) -> bytes:
    d = {"hello": player}
    if token:
        d["token"] = token
    return (json.dumps(d) + "\n").encode("utf-8")


def encode_talk(talking: bool) -> bytes:
    """client -> relay: local mic talking state (drives the in-game indicator)."""
    return (json.dumps({"talk": 1 if talking else 0}) + "\n").encode("utf-8")


def encode_state(st: PlayerState) -> bytes:
    d = {
        "v": PROTO_VERSION,
        "x": st.x, "y": st.y, "z": st.z, "facing": st.facing,
        "area": st.area, "server": st.server,
        "name": st.name, "identity": st.identity,
    }
    return (json.dumps(d, separators=(",", ":")) + "\n").encode("utf-8")


def encode_idle() -> bytes:
    return _IDLE


def encode_error(reason: str) -> bytes:
    return (json.dumps({"error": reason}) + "\n").encode("utf-8")


def decode_line(line: bytes | str) -> dict:
    return json.loads(line)


def state_from_msg(msg: dict, default_server: str = "network") -> PlayerState:
    return PlayerState(
        x=float(msg["x"]), y=float(msg["y"]), z=float(msg["z"]),
        facing=float(msg.get("facing", 0.0)),
        area=str(msg.get("area", "")),
        server=str(msg.get("server", default_server)),
        name=str(msg.get("name", "")),
        identity=str(msg.get("identity", "")),
    )


def encode_roster(entries) -> bytes:
    """Full roster (ALL players) for the per-speaker range plugin. ``entries`` is
    an iterable of ``(name, area, x, y, z, mode)``; ``name`` is the player ACCOUNT
    name (it matches the Mumble username)."""
    d = {"roster": [[str(n), str(a), round(float(x), 3), round(float(y), 3),
                     round(float(z), 3), int(m)] for (n, a, x, y, z, m) in entries]}
    return (json.dumps(d, separators=(",", ":")) + "\n").encode("utf-8")


def roster_from_msg(msg: dict):
    """Parse a roster message into a list of ``(name, area, x, y, z, mode)``."""
    out = []
    for e in msg.get("roster", []):
        try:
            n, a, x, y, z, m = e
            out.append((str(n), str(a), float(x), float(y), float(z), int(m)))
        except (ValueError, TypeError):
            continue
    return out
