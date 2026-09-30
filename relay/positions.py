"""SqlFile provider (RE-free): read the local player's state from the NWN:EE
campaign SQLite database that the server-side NWScript writes.

This works when the bridge and the NWN *server* share a filesystem, i.e. when
you HOST the game (single-player or "host multiplayer") on the same machine that
runs Mumble. The companion NWScript (see ../../nwscript/vc_voice.nss) writes a
row per online PC into a campaign DB in:
    %USERPROFILE%\\Documents\\Neverwinter Nights\\database\\<db>.sqlite3

Table schema (created by the NWScript side):
    CREATE TABLE vc_positions(
        cdkey TEXT PRIMARY KEY, playername TEXT, charname TEXT,
        area TEXT, x REAL, y REAL, z REAL, facing REAL, seq INTEGER);
    CREATE TABLE vc_dm_pos(...)   -- stesse colonne, solo i DM (vedi _DM_COLS)
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Optional

from nwn_voce.state import PlayerState

DEFAULT_DB_NAME = "nwn_voice"

# Area fasulla iniettata nel roster per MUTARE uno speaker a un ascoltatore (voce privata
# DM "Appari solo a"). Il plugin vc_range silenzia uno speaker la cui area != area
# dell'ascoltatore (vedi vc_range.c). Nessuna area di gioco usa questo resref.
PRIV_MUTE_AREA = "\x01vc_priv_mute"

# Una riga ferma da cosi' tanto e' un fantasma. Lo script cancella le righe all'uscita e
# al caricamento del modulo: una riga ferma e' quasi sempre un giocatore in una schermata
# di caricamento, e va tenuto dov'era (se sparisse dal roster lo sentirebbero tutti a
# volume pieno, e lui sentirebbe tutti).
ROSTER_STALE_AFTER = 30.0

# I DM (vc_voice.nss li scrive in vc_dm_pos, NON in vc_positions) sono voci come quelle
# dei giocatori (portata sussurra/parla/urla, dal loro avatar o dal PNG che possiedono),
# con due eccezioni che decide il relay per ogni ascoltatore:
#  - EMERGENZA (range_mode = EMERGENCY_MODE): fuori dal roster degli altri -> il plugin
#    lo lascia a volume pieno: lo sentono tutti, ovunque;
#  - "Isola" attiva: i NON selezionati lo ricevono in un'area fasulla (muto), i selezionati
#    e gli altri DM non lo ricevono affatto (volume pieno).
EMERGENCY_MODE = 3
_DM_COLS = "cdkey, playername, charname, area, x, y, z, facing, range_mode, seq"
_POS_COLS = "cdkey, playername, charname, area, x, y, z, facing, seq"   # poll(): senza range_mode


def default_db_path(db_name: str = DEFAULT_DB_NAME) -> str:
    base = os.path.join(os.path.expanduser("~"), "Documents",
                        "Neverwinter Nights", "database")
    return os.path.join(base, f"{db_name}.sqlite3")


def _s(r, col: str) -> str:
    """Colonna testo di una riga, "" se manca o e' NULL."""
    return str(r[col]).strip() if col in r.keys() and r[col] is not None else ""


def _names(r) -> list:
    """Nome account e nome personaggio (senza doppioni): il plugin abbina il nome
    Mumble all'uno o all'altro."""
    names = []
    for nm in (_s(r, "playername"), _s(r, "charname")):
        if nm and nm.lower() not in [e.lower() for e in names]:
            names.append(nm)
    if not names and _s(r, "cdkey"):
        names.append(_s(r, "cdkey"))
    return names


def _mode(r) -> int:
    """Portata (0 sussurra, 1 parla, 2 urla, EMERGENCY_MODE); 1 se manca."""
    return (int(r["range_mode"]) if "range_mode" in r.keys() and r["range_mode"] is not None
            else 1)


def _entries(r, mode: int) -> list:
    """Voci del roster di una riga: la stessa posizione sotto ognuno dei suoi nomi."""
    area, x, y, z = _s(r, "area"), float(r["x"]), float(r["y"]), float(r["z"])
    return [(nm, area, x, y, z, mode) for nm in _names(r)]


class SqlFileProvider:
    name = "sqlfile"

    def __init__(self, db_path: Optional[str] = None,
                 player: Optional[str] = None,
                 server: str = "localhost",
                 stale_after: float = 2.0,
                 roster_stale_after: float = ROSTER_STALE_AFTER) -> None:
        self.db_path = db_path or default_db_path()
        self.player = player          # match charname/playername/cdkey; None => most recent
        self.server = server
        self.stale_after = stale_after
        self.roster_stale_after = roster_stale_after
        self._conn: Optional[sqlite3.Connection] = None
        self._last_seq: Optional[int] = None
        self._last_change = 0.0
        # Per-player freshness for the roster TTL (#2): key -> (last_seq, last_change).
        # A row whose seq stops advancing for > roster_stale_after is a ghost (player
        # left / server restarted with stale rows) and is dropped from the roster.
        self._roster_seen: dict = {}

    def open(self) -> None:
        self._try_connect()

    def _try_connect(self) -> bool:
        if self._conn is not None:
            return True
        if not os.path.exists(self.db_path):
            return False
        # NWN keeps the campaign DB open while running and uses WAL journaling.
        # CRITICAL (Docker/multi-process WAL): a strict ?mode=ro connection takes a
        # snapshot and does NOT see commits made by another process/container after
        # it opens (it can't write the -shm shared-memory index to advance its WAL
        # read mark). Result: the relay served a FROZEN, stale roster -> no
        # positional attenuation. So we try a normal RW handle FIRST, constrained
        # with PRAGMA query_only (reads live WAL updates, never writes tables), and
        # fall back to mode=ro ONLY if the file is genuinely not writable. We probe
        # with PRAGMA schema_version to force real file access (NOT a table query,
        # so a not-yet-created vc_positions doesn't reject a valid connection).
        for mode in ("rw", "ro"):
            conn = None
            try:
                if mode == "ro":
                    conn = sqlite3.connect(f"file:{self.db_path}?mode=ro",
                                           uri=True, timeout=0.3,
                                           check_same_thread=False)
                else:
                    conn = sqlite3.connect(self.db_path, timeout=0.3,
                                           check_same_thread=False)
                    conn.execute("PRAGMA query_only=ON;")
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA schema_version;").fetchone()
                self._conn = conn
                return True
            except sqlite3.Error:
                if conn is not None:
                    try:
                        conn.close()
                    except sqlite3.Error:
                        pass
                continue
        self._conn = None
        return False

    def _query_row(self):
        assert self._conn is not None
        # Anche i DM hanno una posizione (vc_dm_pos): cosi' l'app del DM risulta "in
        # gioco" e il pan stereo segue il suo sguardo.
        if self.player:
            where = " WHERE charname=? OR playername=? OR cdkey=?"
            args = (self.player, self.player, self.player)
        else:
            where, args = "", ()
        both = (f"SELECT {_POS_COLS} FROM vc_positions{where} UNION ALL "
                f"SELECT {_POS_COLS} FROM vc_dm_pos{where} ORDER BY seq DESC LIMIT 1")
        try:
            return self._conn.execute(both, args + args).fetchone()
        except sqlite3.OperationalError:
            # vc_dm_pos non esiste ancora (modulo vecchio): solo i giocatori
            return self._conn.execute(
                f"SELECT {_POS_COLS} FROM vc_positions{where} ORDER BY seq DESC LIMIT 1",
                args).fetchone()

    def poll(self) -> Optional[PlayerState]:
        if not self._try_connect():
            return None
        try:
            row = self._query_row()
        except sqlite3.Error:
            # DB may be mid-write or table missing yet; drop the connection and retry later.
            try:
                self._conn.close()  # type: ignore[union-attr]
            except sqlite3.Error:
                pass
            self._conn = None
            return None
        if row is None:
            return None

        seq = row["seq"] if "seq" in row.keys() else None
        now = time.perf_counter()
        if seq != self._last_seq:
            self._last_seq = seq
            self._last_change = now
        elif now - self._last_change > self.stale_after:
            # seq stopped advancing => player left / server stopped writing.
            return None

        return PlayerState(
            x=float(row["x"]), y=float(row["y"]), z=float(row["z"]),
            facing=float(row["facing"]),
            area=str(row["area"]),
            server=self.server,
            name=str(row["charname"] or row["playername"] or ""),
            identity=str(row["cdkey"] or row["playername"] or row["charname"] or ""),
        )

    def _fresh(self, key: str, seq, now: float) -> bool:
        """Registra l'avanzamento di ``seq`` per ``key``; False se e' fermo da troppo."""
        prev = self._roster_seen.get(key)
        if prev is None or prev[0] != seq:
            self._roster_seen[key] = (seq, now)      # advanced (or first sighting): fresh
            return True
        return now - prev[1] <= self.roster_stale_after

    def _dm_rows(self):
        """Righe dei DM; None se la tabella non c'e' (modulo vecchio, che non scrive i
        DM da nessuna parte), [] se il DB e' occupato."""
        try:
            return self._conn.execute(f"SELECT {_DM_COLS} FROM vc_dm_pos").fetchall()
        except sqlite3.OperationalError as e:
            return None if "no such table" in str(e) else []
        except sqlite3.Error:
            return []

    def _is_me(self, r) -> bool:
        p = (self.player or "").lower()
        return bool(p) and p in (_s(r, "playername").lower(), _s(r, "charname").lower(),
                                 _s(r, "cdkey").lower())

    def read_roster(self):
        """All current players as roster rows for the shared-memory roster the
        Mumble plugin reads: ``(name, area, x, y, z, mode)``. Empty list on any
        error. Robust to either schema (with or without the ``range_mode`` column).

        IDENTITY (#1): the plugin matches a speaker by comparing their Mumble
        username to this ``name`` (case-insensitively). A player might set their
        nickname to EITHER their NWN account name (playername) OR their character
        name (charname). To match whichever they chose, we emit the SAME position
        twice -- once per name -- when the two differ. This keeps the binary roster
        format unchanged (no plugin rebuild) at the cost of up to 2 slots/player
        (so ~32 concurrent players within MAX_ENTRIES=64; ample for <=30 users).

        GHOSTS (#2): we track each player's ``seq``; once it stops advancing for
        > roster_stale_after seconds the row is a ghost and is dropped, so nobody
        is heard at a frozen position. Ghost detection requires a non-null ``seq``
        value: a NULL/absent seq compares equal to itself, so such a row would be
        treated as permanently frozen and dropped after roster_stale_after. The
        production NWScript (vc_voice.nss) always binds a non-null seq, so this
        only matters for off-spec writers.

        DM: vedi _dm_entries. Le loro voci vanno in TESTA: il plugin prende la prima
        voce col nome giusto, e se il roster supera MAX_ENTRIES si taglia la coda, non
        queste."""
        if not self._try_connect():
            return []
        try:
            rows = self._conn.execute("SELECT * FROM vc_positions").fetchall()
        except sqlite3.Error:
            try:
                self._conn.close()  # type: ignore[union-attr]
            except sqlite3.Error:
                pass
            self._conn = None
            return []
        dm_rows = self._dm_rows()
        now = time.perf_counter()
        out = []
        alive = set()
        for r in rows:
            keys = r.keys()
            # Freshness key: cdkey is the stable per-player id; fall back to a name.
            skey = _s(r, "cdkey") or _s(r, "playername") or _s(r, "charname")
            if not skey:
                continue
            alive.add(skey)
            if not self._fresh(skey, r["seq"] if "seq" in keys else None, now):
                continue                          # seq frozen too long => ghost, drop
            out += _entries(r, _mode(r))
        # DM presenti (riga che avanza): le loro voci dipendono da chi ascolta.
        dms, me_dm = ({} if dm_rows is not None else None), None
        for r in dm_rows or []:
            key = "dm:" + (_s(r, "cdkey") or _s(r, "playername") or _s(r, "charname"))
            alive.add(key)
            if not self._fresh(key, r["seq"], now):
                continue
            dms[_s(r, "cdkey")] = r
            if self._is_me(r):
                me_dm = r
        # Drop freshness state for players no longer in the table (bound growth).
        for k in [k for k in self._roster_seen if k not in alive]:
            del self._roster_seen[k]
        return self._dm_entries(rows, dms, me_dm) + out

    def _my_cdkey(self, rows) -> str:
        """cdkey di QUESTO ascoltatore (self.player puo' essere account o char name)."""
        for r in rows:
            if self._is_me(r):
                return _s(r, "cdkey")
        return ""

    def _active_sessions(self) -> dict:
        """Sessioni "Isola" accese: {cdkey del DM: [nome account, nome personaggio]}.
        Vuoto se le tabelle vc_priv_* non esistono ancora o il DB e' occupato."""
        try:
            sess = self._conn.execute(
                "SELECT spk, spk_p, spk_c FROM vc_priv_session WHERE active=1").fetchall()
        except sqlite3.Error:
            return {}
        return {str(s["spk"] or ""): [str(s["spk_p"] or ""), str(s["spk_c"] or "")]
                for s in sess}

    def _is_member(self, spk: str, my: str) -> bool:
        if not my:
            return False
        try:
            return self._conn.execute(
                "SELECT 1 FROM vc_priv_member WHERE spk=? AND mem=?",
                (spk, my)).fetchone() is not None
        except sqlite3.Error:
            return False

    def _dm_entries(self, rows, dms, me_dm) -> list:
        """Le voci dei DM nel roster di QUESTO ascoltatore (vanno in testa, vedi read_roster):
        - io, se sono un DM: sempre, per sentire chi ho intorno;
        - DM in emergenza: nessuna voce -> il plugin lo lascia a volume pieno, ovunque;
        - DM con "Isola" accesa (allucinazione): chi e' selezionato, e gli altri DM, lo
          sentono come un giocatore (distanza + portata); per tutti gli altri e' in
          un'area fasulla -> muto;
        - altrimenti: come un giocatore.
        La sessione di un DM che non c'e' (uscito, tornato giocatore) non conta: la spegne
        lo script, ma se non ci riuscisse nessuno resterebbe mutato per sempre.
        ``dms`` None = modulo vecchio senza vc_dm_pos: il DM non e' nel roster (lo sentono
        tutti) e "Isola" lo muta ai non selezionati, come prima."""
        sess = self._active_sessions()
        my = _s(me_dm, "cdkey") if me_dm is not None else self._my_cdkey(rows)
        out = []

        def mute(names):
            out.extend((nm, PRIV_MUTE_AREA, 0.0, 0.0, 0.0, 1) for nm in names if nm)

        if dms is None:
            for spk, names in sess.items():
                if spk != my and not self._is_member(spk, my):
                    mute(names)
            return out
        if me_dm is not None:
            out += _entries(me_dm, _mode(me_dm))
        for spk, d in dms.items():
            if d is me_dm:
                continue
            mode = _mode(d)
            if mode == EMERGENCY_MODE:
                continue                     # emergenza: lo sentono tutti
            if spk in sess and me_dm is None and not self._is_member(spk, my):
                names = _names(d)
                mute(names + [nm for nm in sess[spk]
                              if nm.lower() not in [e.lower() for e in names]])
                continue
            out += _entries(d, mode)
        return out

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
