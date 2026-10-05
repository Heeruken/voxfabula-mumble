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

CINEMA (Companion 1.3+): video a schermo intero chiesti da uno script del server.
client -> relay, nel hello:  {"hello": .., "cinema": 1}   # so mostrare i video
relay -> client:             {"video": "<nome>", "id": "<id richiesta>"}
client -> relay:             {"video_esito": "<id>", "esito": "<esito>"}
    esito: visto | saltato | errore | mancante | disattivato | occupato
Un client senza "cinema" nel hello non riceve mai video: il relay risponde
subito "assente" allo script, che fa entrare il personaggio senza video.
I client vecchi ignorano i messaggi che non conoscono.

PALCO (Companion 1.3.1+): scene interattive in un overlay sopra il gioco (es. esaminare un
oggetto in 3D), aperte da uno script del server a uno o piu' giocatori. Le decisioni (tiri,
cosa si scopre) le prende SEMPRE lo script: il client mostra e riferisce.
client -> relay, nel hello:  {"hello": .., "palco": 1}
relay -> client:  {"scena": "<sid>", "tipo": "<tipo>", "dati": {..}}   # apri
                  {"scena_msg": "<sid>", "dati": {..}}                 # dal server (o "vista" di un altro)
                  {"scena_chiudi": "<sid>"}                            # il server la chiude
client -> relay:  {"scena_ev": "<sid>", "ev": "<nome>", "dati": {..}}
    ev "vista"  = come il giocatore guarda la scena: il relay la gira agli altri partecipanti
    ev "chiusa" = il giocatore l'ha chiusa (dati.esito); ogni altro ev va allo script
"""

from __future__ import annotations

import json

from .state import PlayerState

PROTO_VERSION = 1

_IDLE = b'{"idle":true}\n'


ESITI_VIDEO = ("visto", "saltato", "errore", "mancante", "disattivato", "occupato")


def encode_hello(player: str, token: str | None = None, cinema: bool = False,
                 palco: bool = False) -> bytes:
    d = {"hello": player}
    if token:
        d["token"] = token
    if cinema:
        d["cinema"] = 1
    if palco:
        d["palco"] = 1
    return (json.dumps(d) + "\n").encode("utf-8")


SCENA_DATI_MAX = 16384      # byte di "dati" in un messaggio di scena (oltre: scartato)
SID_RE_CHARS = "0123456789abcdef"


def sid_ok(sid) -> bool:
    return isinstance(sid, str) and 1 <= len(sid) <= 32 and all(c in SID_RE_CHARS for c in sid)


def _riga(d: dict) -> bytes:
    return (json.dumps(d, separators=(",", ":")) + "\n").encode("utf-8")


def encode_scena(sid: str, tipo: str, dati: dict) -> bytes:
    """relay -> client: apri la scena ``sid`` di tipo ``tipo``."""
    return _riga({"scena": sid, "tipo": tipo, "dati": dati})


def encode_scena_msg(sid: str, dati: dict) -> bytes:
    """relay -> client: un messaggio per la scena aperta ``sid``."""
    return _riga({"scena_msg": sid, "dati": dati})


def encode_scena_chiudi(sid: str) -> bytes:
    return _riga({"scena_chiudi": sid})


def encode_scena_ev(sid: str, ev: str, dati: dict | None = None) -> bytes:
    """client -> relay: il giocatore ha fatto ``ev`` nella scena ``sid``."""
    return _riga({"scena_ev": sid, "ev": ev, "dati": dati or {}})


def encode_video(nome: str, rid: str) -> bytes:
    """relay -> client: mostra il video ``nome`` (richiesta ``rid``)."""
    return (json.dumps({"video": nome, "id": rid}) + "\n").encode("utf-8")


def encode_video_esito(rid: str, esito: str) -> bytes:
    """client -> relay: com'e' andata la richiesta ``rid``."""
    if esito not in ESITI_VIDEO:
        esito = "errore"
    return (json.dumps({"video_esito": rid, "esito": esito}) + "\n").encode("utf-8")


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
