"""Prova completa del cinema in partita LOCALE (giocatore singolo), senza server e senza Mumble.

    python prove_cinema/prova_completa.py        (o il .bat sul Desktop)

Avvia sul tuo PC: il relay (porta 27899) sul database della voce di NWN
(Documenti\\Neverwinter Nights\\database\\nwn_voice.sqlite3) e la parte "cinema" del
Companion (regia + lettore veri). Poi giochi VoxFabula0_3dung in locale: quando lo script
del modulo chiede un video, lo vedi sopra il gioco come lo vedranno i giocatori.
Il nome del giocatore lo prende da solo dalla prima posizione che il modulo scrive.
"""
import os
import shutil
import sqlite3
import sys
import threading
import time

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))

from nwn_voce import paths, regia                 # noqa: E402
from nwn_voce.relay_client import RelayClient     # noqa: E402
from relay import server as relay_server          # noqa: E402

PORTA = 27899
DB = os.path.join(os.path.expanduser("~"), "Documents", "Neverwinter Nights", "database",
                  "nwn_voice.sqlite3")


def scrivi(chiave, **dati):
    if chiave == "attivita":
        print("  [registro]", dati.get("voce"), {k: v for k, v in dati.items() if k not in ("voce", "ora")})


def nome_giocatore():
    """Aspetta la prima riga di vc_positions scritta da una partita in corso."""
    print("Avvia NWN, Nuova partita -> Modulo -> VoxFabula0_3dung, ed entra in gioco...")
    visto = None
    while True:
        try:
            c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=0.5)
            r = c.execute("SELECT playername, charname, seq FROM vc_positions ORDER BY seq DESC LIMIT 1").fetchone()
            c.close()
        except sqlite3.Error:
            r = None
        if r and r[0]:
            if visto is None:
                visto = r[2]
            elif r[2] != visto:            # la riga si aggiorna: la partita e' viva
                return r[0], r[1]
        time.sleep(1.0)


def main():
    if not os.path.exists(DB):
        print("Non trovo", DB, "- apri almeno una volta il modulo in NWN.")
    cartella = regia.cartella_video()
    # il video sigillato, come lo pubblichera' il server (preparato con catalogo.py)
    for nome in ("ingresso_ossario.vfv",):
        shutil.copy(os.path.join(QUI, "pubblica", nome), os.path.join(cartella, nome))
    print("Video del Companion in:", cartella)

    stop = threading.Event()
    pronto = threading.Event()
    threading.Thread(target=relay_server.serve, daemon=True,
                     kwargs=dict(db_path=DB, host="127.0.0.1", port=PORTA, server_id="prova",
                                 stop=stop, ready=pronto, talk=False)).start()
    if not pronto.wait(5):
        sys.exit("il relay di prova non parte (porta %d occupata?)" % PORTA)

    acc, pg = nome_giocatore()
    print(f"In gioco: {pg} (account {acc}). Il Companion di prova e' collegato.")
    print("Esci dalla Cappella verso l'Attracco e usa il Varco dell'Ossario: deve partire il video.")
    print("Per chiudere la prova: chiudi questa finestra.\n")
    reg = regia.Registro(scrivi)
    r = regia.Regia(reg)
    c = RelayClient("127.0.0.1", PORTA, player=acc)
    c.on_video = lambda nome, rid: (print(f"-> il server chiede il video '{nome}'"),
                                    r.richiesta(nome, rid, c.send_video_esito))
    c.open()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        c.close()
        stop.set()


if __name__ == "__main__":
    main()
