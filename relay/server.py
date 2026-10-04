"""Position relay (server side, RE-free).

Runs on the machine that can read the NWN campaign DB written by
``nwscript/vc_voice.nss`` (i.e. the NWN *server* host). Remote bridge clients
connect over TCP, announce who they are, and receive a heartbeated stream of
THEIR OWN position as line-delimited JSON (see net_protocol). The client feeds
that into Mumble's shared memory. This is how cross-machine positional audio
works without reverse-engineering the game client.

The relay ALSO streams a throttled (~0.2s) full roster ({"roster":[...]}) of
all players' positions and range modes to every client; this is consumed by the
per-speaker range plugin so each client knows where everyone else is.

Run:
    python -m relay --server myworld --port 27890
    python -m relay --db "C:\\path\\to\\nwn_voice.sqlite3" --token secret

Each client must pass a matching ``--player`` (character/player/CD-key) and, if
set, the same ``--token``.
"""

from __future__ import annotations

import argparse
import socket
import sqlite3
import threading
import time

from nwn_voce import net_protocol as proto
from .positions import SqlFileProvider, default_db_path
from .talk_listener import TalkListener
from .fantoccio import EchoBot


# Video chiesti dagli script del modulo (vf_cinema.nss), uno per riga:
#   stato 0 = chiesto dallo script, 1 = inviato al client, 2 = concluso (esito scritto).
# Esiti oltre a quelli del client (net_protocol.ESITI_VIDEO): "assente" (nessun client
# capace di video collegato) e "disconnesso" (client caduto durante il video).
_CINEMA_TABLE = ("CREATE TABLE IF NOT EXISTS vf_cinema (id TEXT PRIMARY KEY, cdkey TEXT, "
                 "video TEXT, stato INTEGER, esito TEXT, t INTEGER);")
CINEMA_PERIOD = 0.5          # ogni quanto il relay guarda se ci sono video da mandare
CINEMA_RESOLVE = 5.0         # ogni quanto ricontrolla nome client -> cdkey
CINEMA_RETRY_FOR = 60.0      # per quanto si riprova a scrivere un esito se il DB e' occupato


def _log(msg: str) -> None:
    t = time.time()
    stamp = time.strftime('%H:%M:%S', time.localtime(t)) + f".{int((t % 1) * 1000):03d}"
    print(f"[relay {stamp}] {msg}", flush=True)


class _TalkWriter:
    """Writes ``vc_client.talking`` into the campaign DB so the in-game 'who's
    talking' indicator works for ALL clients, not just a local host (#4).

    Server-side, low rate (only on mic on/off), and CRASH-PROOF: a single shared
    write connection guarded by a lock, a generous busy_timeout (~5s) to coexist
    with nwserver's writes, and every DB error swallowed -- a failure here must
    NEVER block serving positions. The client is known by name (from its hello);
    we resolve its cdkey via vc_positions, which is how the NWScript keys talk."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _connect(self) -> bool:
        if self._conn is not None:
            return True
        try:
            c = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
            c.execute("PRAGMA busy_timeout=5000;")   # coesiste con nwserver (doc SQLite: ~5s)
            c.execute("CREATE TABLE IF NOT EXISTS vc_client "
                      "(cdkey TEXT PRIMARY KEY, talking INTEGER, "
                      "range_request INTEGER, seq INTEGER);")
            c.execute(_CINEMA_TABLE)
            c.commit()
            self._conn = c
            return True
        except sqlite3.Error:
            self._conn = None
            return False

    def _resolve_cdkey(self, player: str):
        """Mappa il nick Mumble alla CD key NWN, in modo tollerante: il nick puo'
        differire dal nome account per maiuscole/minuscole e, soprattutto, per il
        suffisso numerico che Mumble aggiunge quando il nick e' gia' in uso
        ('Hiruken' -> 'Hiruken2' per una sessione fantasma o un riconnesso).
        Cerca anche tra i DM (vc_dm_pos): l'icona si accende pure sopra di loro."""
        match = "WHERE playername=? COLLATE NOCASE OR charname=? COLLATE NOCASE"
        q = (f"SELECT cdkey, seq FROM vc_positions {match} UNION ALL "
             f"SELECT cdkey, seq FROM vc_dm_pos {match} ORDER BY seq DESC LIMIT 1")
        try:
            self._conn.execute("SELECT 1 FROM vc_dm_pos LIMIT 0")
            both = True
        except sqlite3.OperationalError:
            # modulo vecchio senza vc_dm_pos: solo i giocatori
            q = f"SELECT cdkey FROM vc_positions {match} ORDER BY seq DESC LIMIT 1"
            both = False

        def find(name):
            args = (name, name, name, name) if both else (name, name)
            row = self._conn.execute(q, args).fetchone()
            return row[0] if row and row[0] else None

        found = find(player)                                        # 1) esatto (case-insensitive)
        base = player.rstrip("0123456789")                         # 2) togli suffisso numerico Mumble
        if not found and base and base != player:
            found = find(base)
        return found

    def set_talking(self, player: str, talking: bool) -> None:
        if not player:
            return
        with self._lock:
            if not self._connect():
                return
            try:
                cdkey = self._resolve_cdkey(player)
                if not cdkey:
                    return                       # player not in-game yet: nothing to key
                self._conn.execute(
                    "INSERT INTO vc_client (cdkey, talking) VALUES (?,?) "
                    "ON CONFLICT(cdkey) DO UPDATE SET talking=excluded.talking",
                    (cdkey, 1 if talking else 0))
                self._conn.commit()
            except sqlite3.Error:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None               # drop & reconnect next time

    # ---- cinema: video chiesti dagli script (tabella vf_cinema, vedi _CINEMA_TABLE) ----
    def _cinema_do(self, fn, default=None):
        """Esegue ``fn(conn)`` col lucchetto; ogni errore del DB = ``default`` (mai un crash)."""
        with self._lock:
            if not self._connect():
                return default
            try:
                out = fn(self._conn)
                self._conn.commit()
                return out
            except sqlite3.Error:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None
                return default

    def cdkey_of(self, player: str):
        if not player:
            return None
        return self._cinema_do(lambda c: self._resolve_cdkey(player))

    def cinema_take(self, cdkey: str) -> list:
        """Le richieste nuove (stato 0) di questo giocatore, segnate come inviate (1)."""
        def fn(c):
            rows = c.execute("SELECT id, video FROM vf_cinema WHERE cdkey=? AND stato=0 "
                             "ORDER BY t", (cdkey,)).fetchall()
            for rid, _v in rows:
                c.execute("UPDATE vf_cinema SET stato=1 WHERE id=? AND stato=0", (rid,))
            return [(str(r), str(v)) for r, v in rows]
        return self._cinema_do(fn, [])

    def cinema_close(self, cdkey: str, rid: str, esito: str):
        """Esito di una richiesta: solo il client del giocatore giusto puo' chiuderla.
        True = chiusa; False = non sua o gia' chiusa; None = DB occupato (riprovare)."""
        return self._cinema_do(lambda c: c.execute(
            "UPDATE vf_cinema SET stato=2, esito=? WHERE id=? AND cdkey=? AND stato<2",
            (esito, rid, cdkey)).rowcount > 0, None)

    def cinema_drop(self, cdkey: str, esito: str, only_sent: bool) -> None:
        """Chiude in blocco le richieste aperte di un giocatore (client assente o caduto)."""
        cond = "stato=1" if only_sent else "stato<2"
        self._cinema_do(lambda c: c.execute(
            f"UPDATE vf_cinema SET stato=2, esito=? WHERE cdkey=? AND {cond}", (esito, cdkey)))

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None


def _handle(conn: socket.socket, addr, db_path: str, server_id: str,
            token: str | None, hz: float, stop: threading.Event,
            writer: "_TalkWriter | None" = None,
            fant: dict | None = None) -> None:
    player = None
    prov = None
    try:
        conn.settimeout(10.0)
        stream = conn.makefile("rb")            # ONE buffered reader (hello + talk)
        hello = stream.readline(65536)          # cap: input non autenticato, niente buffer illimitato
        if not hello:
            return
        msg = proto.decode_line(hello)
        if not isinstance(msg, dict):           # non-object JSON -> no .get(), reject cleanly
            conn.sendall(proto.encode_error("bad hello"))
            return
        if token and msg.get("token") != token:
            conn.sendall(proto.encode_error("bad token"))
            _log(f"{addr} rejected (bad token)")
            return
        player = msg.get("hello") or msg.get("player")
        if not player:
            conn.sendall(proto.encode_error("no player"))
            return

        cinema_ok = bool(msg.get("cinema"))       # Companion 1.3+: sa mostrare i video
        cine = {"cdkey": None, "resolved_at": -1e9, "last": 0.0,
                "pend": [], "lock": threading.Lock()}   # esiti da scrivere: (rid, esito, t)
        send_lock = threading.Lock()              # il lettore risponde anche lui sul socket

        prov = SqlFileProvider(db_path=db_path, player=player, server=server_id)
        prov.open()
        conn.settimeout(None)
        period = 1.0 / max(1.0, hz)
        last_roster = 0.0
        roster_period = 0.2          # full roster ~5x/s (drives the range plugin)
        _log(f"{addr} -> player {player!r}")

        # 'Chi parla' e' deciso SOLO dal bot Mumble lato server (talk_listener),
        # unica fonte di verita'. Qui scartiamo SENZA agire quello che il client
        # potrebbe ancora inviare: il vecchio rilevatore mic del client ignorava il
        # mute di Mumble (icona accesa anche da mutati) ed era in conflitto col bot.
        # Continuiamo solo a SVUOTARE il socket per non farlo intasare.
        # Unica eccezione: l'esito di un video ({"video_esito": id, "esito": ..}).
        def _reader():
            try:
                while not stop.is_set():
                    line = stream.readline(65536)   # cap per riga: niente buffer illimitato
                    if not line:
                        break
                    if b"video_esito" not in line or writer is None:
                        continue                    # 'talk' & co.: volutamente ignorati
                    try:
                        m = proto.decode_line(line)
                    except ValueError:
                        continue
                    if isinstance(m, dict):
                        esito = str(m.get("esito", ""))
                        if esito not in proto.ESITI_VIDEO:
                            esito = "errore"
                        with cine["lock"]:          # lo scrive _cinema_tick (anche riprovando)
                            cine["pend"].append((str(m.get("video_esito", "")), esito,
                                                 time.monotonic()))
            except OSError:
                pass
        threading.Thread(target=_reader, daemon=True).start()

        def _cinema_esiti(now: float) -> None:
            """Scrive gli esiti arrivati dal client. Se il DB e' occupato (lo usa anche
            nwserver) l'esito resta in coda e si riprova al giro dopo, per un minuto."""
            cd = cine["cdkey"]
            with cine["lock"]:
                pend, cine["pend"] = cine["pend"], []
            resto = []
            for rid, esito, t0 in pend:
                ok = writer.cinema_close(cd, rid, esito) if cd else None
                if ok:
                    _log(f"{player!r}: video {rid!r} -> {esito}")
                elif ok is False:
                    _log(f"{player!r}: esito per {rid!r} ignorato (non suo o gia' chiuso)")
                elif now - t0 < CINEMA_RETRY_FOR:
                    resto.append((rid, esito, t0))
                else:
                    _log(f"{player!r}: esito per {rid!r} perso (DB occupato da {CINEMA_RETRY_FOR:.0f} s)")
            if resto:
                with cine["lock"]:
                    cine["pend"] = resto + cine["pend"]

        def _cinema_tick(now: float) -> None:
            if writer is None or now - cine["last"] < CINEMA_PERIOD:
                return
            cine["last"] = now
            if cine["pend"]:
                _cinema_esiti(now)
            if now - cine["resolved_at"] >= CINEMA_RESOLVE or not cine["cdkey"]:
                cine["resolved_at"] = now
                cine["cdkey"] = writer.cdkey_of(player) or cine["cdkey"]
            cd = cine["cdkey"]
            if not cd:
                return
            for rid, video in writer.cinema_take(cd):
                if cinema_ok:
                    with send_lock:
                        conn.sendall(proto.encode_video(video, rid))
                    _log(f"{player!r}: video {video!r} ({rid})")
                else:
                    writer.cinema_close(cd, rid, "assente")   # app vecchia: niente video

        # TEST fantoccio: lo PIANTIAMO una volta sola, dove ti vediamo la prima
        # volta (il tuo punto di spawn), e li' RESTA FERMO. Sei TU che ti allontani:
        # la distanza cambia per il tuo movimento reale, come un vero giocatore.
        fant_anchor = None        # (area, x, y, z) fissato al primo avvistamento

        # Heartbeat every tick (state or idle) so client staleness is time-based
        # and disconnects are detected promptly via a failed send.
        while not stop.is_set():
            st = prov.poll()
            with send_lock:
                conn.sendall(proto.encode_state(st) if st is not None
                             else proto.encode_idle())
            now = time.monotonic()
            _cinema_tick(now)
            if now - last_roster >= roster_period:
                last_roster = now
                roster = prov.read_roster()
                if fant and st is not None:
                    if fant_anchor is None:
                        # Coordinate FISSE da CLI (--fantoccio-pos "x,y[,z]") cosi' lo
                        # pianti su un oggetto VISIBILE in gioco; altrimenti dove ti
                        # vediamo la prima volta (l'area e' comunque la tua).
                        if fant.get("pos"):
                            px, py, pz = fant["pos"]
                            fant_anchor = (st.area, px, py, pz)
                        else:
                            fant_anchor = (st.area, st.x, st.y, st.z)
                        _log(f"FANTOCCIO piantato in {fant_anchor[0]} a "
                             f"({fant_anchor[1]:.1f},{fant_anchor[2]:.1f},"
                             f"{fant_anchor[3]:.1f}) per {player!r}")
                    a_area, a_x, a_y, a_z = fant_anchor
                    roster = list(roster) + [
                        (fant["name"], a_area, a_x, a_y, a_z, fant["mode"])]
                if roster:
                    with send_lock:
                        conn.sendall(proto.encode_roster(roster))
            time.sleep(period)
    except (OSError, ValueError, KeyError):
        pass
    finally:
        # NB: non tocchiamo piu' 'talking' alla disconnessione del client: lo gestiscono
        # il bot (spegne all'uscita da Mumble) e lo script in gioco (logout del PG).
        # Un video in corso pero' non finira' mai: lo script deve saperlo subito.
        try:
            cd = cine["cdkey"] if "cine" in locals() else None
            if cd and writer is not None:
                writer.cinema_drop(cd, "disconnesso", only_sent=True)
        except Exception:
            pass
        if prov is not None:
            try:
                prov.close()                 # evita leak dell'handle SQLite a ogni disconnessione
            except Exception:
                pass
        try:
            conn.close()
        except OSError:
            pass
        if player is not None:
            _log(f"{addr} ({player!r}) disconnected")


def serve(db_path: str, host: str = "0.0.0.0", port: int = 27890,
          server_id: str = "nwn", token: str | None = None, hz: float = 15.0,
          stop: threading.Event | None = None,
          ready: threading.Event | None = None,
          mumble_host: str = "127.0.0.1", mumble_port: int = 64738,
          mumble_user: str = "NWN-Voce", mumble_password: str = "",
          mumble_channel: str | None = None, talk: bool = True,
          fant: dict | None = None) -> None:
    """Accept clients until ``stop`` is set. Sets ``ready`` once listening."""
    stop = stop or threading.Event()
    writer = _TalkWriter(db_path)               # shared, crash-proof DB writer (#4)
    if talk:
        # Bot Mumble lato server: sente CHI parla e accende/spegne l'icona (#4).
        TalkListener(writer.set_talking, host=mumble_host, port=mumble_port,
                     username=mumble_user, password=mumble_password,
                     channel=mumble_channel, log=_log).start(stop)
    else:
        _log("talk: bot 'chi parla' disattivato (--no-talk)")
    if fant:
        # TEST: bot-eco che ti ri-trasmette con ritardo, mentre _handle gli
        # assegna una distanza oscillante nel roster. Per provare i raggi da soli.
        EchoBot(host=mumble_host, port=mumble_port, username=fant["name"],
                password=mumble_password, channel=mumble_channel,
                delay=fant["delay"], log=_log).start(stop)
        _log(f"FANTOCCIO attivo: {fant['name']!r} eco {fant['delay']:.1f}s, "
             f"distanza 0..{fant['maxdist']:.0f}m ogni {fant['period']:.0f}s, "
             f"mode={fant['mode']}")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(16)
    srv.settimeout(0.5)
    if ready is not None:
        ready.set()
    _log(f"listening on {host}:{port}  db={db_path}  server={server_id!r}  "
         f"token={'yes' if token else 'no'}")
    try:
        while not stop.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=_handle,
                             args=(conn, addr, db_path, server_id, token, hz, stop,
                                   writer, fant),
                             daemon=True).start()
    finally:
        srv.close()
        writer.close()
        _log("stopped")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="nwn-mumble-relay", description=__doc__)
    p.add_argument("--db", default=None,
                   help="campaign .sqlite3 written by vc_voice.nss "
                        "(default: NWN user database/nwn_voice.sqlite3)")
    p.add_argument("--host", default="0.0.0.0", help="interface to bind")
    p.add_argument("--port", type=int, default=27890)
    p.add_argument("--server", default="nwn",
                   help="server id; SHARED audio context for all its clients")
    p.add_argument("--token", default=None, help="optional shared secret")
    p.add_argument("--hz", type=float, default=15.0, help="heartbeat/update rate")
    p.add_argument("--mumble-host", default="127.0.0.1",
                   help="host del server Mumble da ascoltare per 'chi parla'")
    p.add_argument("--mumble-port", type=int, default=64738,
                   help="porta del server Mumble (default 64738)")
    p.add_argument("--mumble-user", default="NWN-Voce",
                   help="nick del bot ascoltatore nel canale Mumble")
    p.add_argument("--mumble-password", default="",
                   help="password del server Mumble, se impostata")
    p.add_argument("--mumble-channel", default=None,
                   help="canale Mumble in cui entrare (default: root)")
    p.add_argument("--no-talk", action="store_true",
                   help="non avviare il bot 'chi parla'")
    # --- TEST: fantoccio-eco per provare i raggi da soli ---
    p.add_argument("--fantoccio", action="store_true",
                   help="[TEST] bot-eco con distanza oscillante per provare i raggi da solo")
    p.add_argument("--fantoccio-name", default="Fantoccio",
                   help="nick del fantoccio nel canale Mumble e nel roster")
    p.add_argument("--fantoccio-delay", type=float, default=1.5,
                   help="ritardo dell'eco in secondi (default 1.5)")
    p.add_argument("--fantoccio-period", type=float, default=16.0,
                   help="secondi per un ciclo vicino->lontano->vicino (default 16)")
    p.add_argument("--fantoccio-maxdist", type=float, default=45.0,
                   help="distanza massima raggiunta nel ciclo, in metri (default 45)")
    p.add_argument("--fantoccio-pos", default="",
                   help="coordinate FISSE 'x,y[,z]' dove piantarlo (es. su un oggetto "
                        "visibile in gioco); vuoto = dove ti vedo la prima volta")
    p.add_argument("--fantoccio-mode", type=int, default=2, choices=[0, 1, 2],
                   help="0=sussurra(4m) 1=parla(12m) 2=urla(35m) (default 2)")
    a = p.parse_args(argv)
    db = a.db or default_db_path()
    fant = None
    if a.fantoccio:
        fant = {"name": a.fantoccio_name, "delay": a.fantoccio_delay,
                "period": a.fantoccio_period, "maxdist": a.fantoccio_maxdist,
                "mode": a.fantoccio_mode, "pos": None}
        if a.fantoccio_pos.strip():
            try:
                parts = [float(v) for v in a.fantoccio_pos.split(",")]
                fant["pos"] = (parts[0], parts[1], parts[2] if len(parts) > 2 else 0.0)
            except (ValueError, IndexError):
                print(f"--fantoccio-pos non valido: {a.fantoccio_pos!r} (uso primo avvistamento)")
    stop = threading.Event()
    try:
        serve(db, a.host, a.port, a.server, a.token, a.hz, stop,
              mumble_host=a.mumble_host, mumble_port=a.mumble_port,
              mumble_user=a.mumble_user, mumble_password=a.mumble_password,
              mumble_channel=a.mumble_channel, talk=not a.no_talk, fant=fant)
    except KeyboardInterrupt:
        stop.set()
        print("\nstopping.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
