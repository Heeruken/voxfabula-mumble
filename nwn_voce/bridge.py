"""Il ponte: dal relay alla memoria condivisa letta dal plugin vc_range.

Gira in un thread DENTRO l'app (prima era un exe separato che si scompattava in
Temp e partiva nascosto: esattamente il comportamento che insospettisce gli
antivirus). Ogni ciclo:
  1. prende la posizione del nostro personaggio dal relay e la scrive, smussata,
     in "MumbleLink" (da li' vc_range legge lo sguardo per il pan stereo);
  2. ~10 volte al secondo scrive il roster di tutti in "nwn_voice_roster"
     (da li' vc_range calcola distanza e portata di ogni voce).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, Optional

from . import coords
from .mumble_link import MumbleLink
from .relay_client import RelayClient
from .shared_state import RosterWriter
from .state import PlayerState

log = logging.getLogger(__name__)

CONTEXT_PREFIX = b"nwnmumble"
LINK_NAME = "Neverwinter Nights"

# Le posizioni arrivano a ~5 Hz, a gradini: scivoliamo verso il bersaglio
# (one-pole, tau ~200 ms) cosi' il pan non "gratta". Un salto > 15 m e' un
# teletrasporto o un cambio d'area: niente scivolata, si salta.
SMOOTH_TAU = 0.2
SNAP_DIST = 15.0


def build_context(state: PlayerState) -> bytes:
    raw = b"\x00".join([
        CONTEXT_PREFIX,
        state.server.encode("utf-8", "replace"),
        state.area.encode("utf-8", "replace"),
    ])
    return raw[:256]


def run(source: RelayClient, stop: threading.Event,
        on_live: Optional[Callable[[bool], None]] = None,
        rate_hz: float = 50.0, roster_hz: float = 10.0) -> None:
    """Gira finche' ``stop`` non viene impostato. ``on_live(True/False)`` viene
    chiamato solo quando lo stato CAMBIA: True = arrivano davvero le posizioni
    del nostro personaggio (siamo in gioco), False = non ancora / non piu'."""
    period = 1.0 / max(1.0, rate_hz)
    roster_period = 1.0 / max(1.0, roster_hz)
    last_roster = 0.0
    last_roster_error = 0.0
    sm_pos = sm_front = None
    last_t = time.perf_counter()
    live = None  # sconosciuto: il primo ciclo notifica sempre

    try:
        roster = RosterWriter()
    except Exception:  # noqa: BLE001 -- senza roster la voce resta a volume pieno
        log.exception("memoria condivisa del roster non disponibile")
        roster = None

    try:
        with MumbleLink(name=LINK_NAME) as link:
            active = False
            try:
                while not stop.is_set():
                    loop_start = time.perf_counter()
                    try:
                        state = source.poll()
                    except Exception:  # noqa: BLE001
                        state = None

                    if state is not None:
                        tpos = coords.nwn_pos_to_mumble(state.x, state.y, state.z)
                        tfront = coords.nwn_facing_to_front(state.facing)
                        now_t = time.perf_counter()
                        dt = max(0.0, min(0.25, now_t - last_t))
                        last_t = now_t
                        alpha = 1.0 - math.exp(-dt / SMOOTH_TAU) if dt > 0 else 0.0
                        if sm_pos is None or any(abs(tpos[i] - sm_pos[i]) > SNAP_DIST for i in range(3)):
                            sm_pos, sm_front = list(tpos), list(tfront)
                        else:
                            for i in range(3):
                                sm_pos[i] += (tpos[i] - sm_pos[i]) * alpha
                                sm_front[i] += (tfront[i] - sm_front[i]) * alpha
                        n = math.sqrt(sum(c * c for c in sm_front))
                        front = tuple(c / n for c in sm_front) if n > 1e-6 else tfront
                        link.update(avatar_pos=tuple(sm_pos), avatar_front=front,
                                    avatar_top=coords.TOP,
                                    identity=state.identity or state.name,
                                    context=build_context(state))
                        active = True
                    elif active:
                        link.deactivate()          # uscito dal gioco: niente fermo-immagine
                        active = False
                        sm_pos = None

                    if active != live:
                        live = active
                        if on_live is not None:
                            on_live(live)

                    if roster is not None:
                        now_r = time.perf_counter()
                        if now_r - last_roster >= roster_period:
                            last_roster = now_r
                            try:
                                roster.write(source.read_roster())
                            except Exception:  # noqa: BLE001
                                if now_r - last_roster_error >= 30.0:
                                    last_roster_error = now_r
                                    log.exception("scrittura roster fallita")

                    elapsed = time.perf_counter() - loop_start
                    if elapsed < period:
                        stop.wait(period - elapsed)
            finally:
                link.deactivate()
    finally:
        if roster is not None:
            roster.close()
