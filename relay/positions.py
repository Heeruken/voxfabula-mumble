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


def default_db_path(db_name: str = DEFAULT_DB_NAME) -> str:
    base = os.path.join(os.path.expanduser("~"), "Documents",
                        "Neverwinter Nights", "database")
    return os.path.join(base, f"{db_name}.sqlite3")


class SqlFileProvider:
    name = "sqlfile"

    def __init__(self, db_path: Optional[str] = None,
                 player: Optional[str] = None,
                 server: str = "localhost",
                 stale_after: float = 2.0) -> None:
        self.db_path = db_path or default_db_path()
        self.player = player          # match charname/playername/cdkey; None => most recent
        self.server = server
        self.stale_after = stale_after
        self._conn: Optional[sqlite3.Connection] = None
        self._last_seq: Optional[int] = None
        self._last_change = 0.0
        # Per-player freshness for the roster TTL (#2): key -> (last_seq, last_change).
        # A row whose seq stops advancing for > stale_after is a ghost (player left
        # / server restarted with stale rows) and is dropped from the roster.
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
        if self.player:
            sql = ("SELECT * FROM vc_positions WHERE charname=? OR playername=? "
                   "OR cdkey=? ORDER BY seq DESC LIMIT 1")
            args = (self.player, self.player, self.player)
        else:
            sql = "SELECT * FROM vc_positions ORDER BY seq DESC LIMIT 1"
            args = ()
        cur = self._conn.execute(sql, args)
        return cur.fetchone()

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

        GHOSTS (#2): the campaign DB has no DELETE-on-disconnect, so a row lingers
        after a player leaves (and survives a server restart). We track each
        player's ``seq``; once it stops advancing for > stale_after seconds the row
        is a ghost and is dropped, so nobody is heard at a frozen position. Ghost
        detection requires a non-null ``seq`` value: a NULL/absent seq compares
        equal to itself, so such a row would be treated as permanently frozen and
        dropped after stale_after. The production NWScript (vc_voice.nss) always
        binds a non-null seq, so this only matters for off-spec writers."""
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
        now = time.perf_counter()
        seen = self._roster_seen
        out = []
        alive = set()
        for r in rows:
            keys = r.keys()
            cdkey = (str(r["cdkey"]) if "cdkey" in keys and r["cdkey"] is not None
                     else "")
            pname = str(r["playername"] or "").strip() if "playername" in keys else ""
            cname = str(r["charname"] or "").strip() if "charname" in keys else ""
            seq = r["seq"] if "seq" in keys else None
            # Freshness key: cdkey is the stable per-player id; fall back to a name.
            skey = cdkey or pname or cname
            if not skey:
                continue
            alive.add(skey)
            prev = seen.get(skey)
            if prev is None or prev[0] != seq:
                seen[skey] = (seq, now)          # advanced (or first sighting): fresh
            elif now - prev[1] > self.stale_after:
                continue                          # seq frozen too long => ghost, drop
            mode = (int(r["range_mode"]) if ("range_mode" in keys
                    and r["range_mode"] is not None) else 1)
            area = str(r["area"] or "")
            x, y, z = float(r["x"]), float(r["y"]), float(r["z"])
            # Emit under both the account and character name (deduped); the plugin
            # matches the Mumble username against either.
            names = []
            for nm in (pname, cname):
                if nm and nm.lower() not in [e.lower() for e in names]:
                    names.append(nm)
            if not names and cdkey:
                names.append(cdkey)
            for nm in names:
                out.append((nm, area, x, y, z, mode))
        # Drop freshness state for players no longer in the table (bound growth).
        for k in [k for k in seen if k not in alive]:
            del seen[k]
        # Voce privata DM ("Appari solo a"): muta i DM-speaker per i NON-selezionati.
        return self._apply_private(out, rows)

    def _my_cdkey(self, rows) -> str:
        """cdkey di QUESTO ascoltatore (self.player puo' essere account o char name)."""
        p = (self.player or "").lower()
        if not p:
            return ""
        for r in rows:
            keys = r.keys()
            ck = (str(r["cdkey"]) if "cdkey" in keys and r["cdkey"] is not None else "")
            pn = (str(r["playername"]) if "playername" in keys and r["playername"] else "").lower()
            cn = (str(r["charname"]) if "charname" in keys and r["charname"] else "").lower()
            if p == pn or p == cn or p == ck.lower():
                return ck
        return ""

    def _apply_private(self, out, rows):
        """Per ogni sessione 'Appari solo a' ATTIVA: se IO (l'ascoltatore) NON sono un membro
        (e non sono io il DM-speaker), inietto il DM nel mio roster con un'area fasulla ->
        il plugin lo silenzia. I membri restano fuori dal roster -> lo sentono (audio normale).
        Robusto se le tabelle vc_priv_* non esistono ancora (nessuna sessione)."""
        assert self._conn is not None
        try:
            sess = self._conn.execute(
                "SELECT spk, spk_p, spk_c FROM vc_priv_session WHERE active=1").fetchall()
        except sqlite3.Error:
            return out                       # tabella non ancora creata o DB occupato: nessun filtro
        if not sess:
            return out
        my = self._my_cdkey(rows)
        for s in sess:
            spk = str(s["spk"] or "")
            if my and my == spk:
                continue                     # sono io il DM-speaker: non mi muto
            is_member = False
            if my:
                try:
                    is_member = self._conn.execute(
                        "SELECT 1 FROM vc_priv_member WHERE spk=? AND mem=?",
                        (spk, my)).fetchone() is not None
                except sqlite3.Error:
                    is_member = False
            if is_member:
                continue                     # selezionato: sento il DM normalmente (full)
            # non-membro: inietto il DM (entrambi i nomi) in area fasulla -> mutato
            for nm in {str(s["spk_p"] or ""), str(s["spk_c"] or "")}:
                if nm:
                    out.append((nm, PRIV_MUTE_AREA, 0.0, 0.0, 0.0, 1))
        return out

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
