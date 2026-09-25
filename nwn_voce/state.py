"""Lo stato del personaggio del giocatore, come arriva dal server."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlayerState:
    """Posizione nel mondo NWN (metri / gradi)."""
    x: float
    y: float
    z: float
    facing: float = 0.0          # gradi, antiorario da Est
    area: str = ""               # resref dell'area
    server: str = ""             # id del server (parte del contesto audio)
    name: str = ""               # nome personaggio / account
    identity: str = ""           # id univoco del giocatore nel contesto
