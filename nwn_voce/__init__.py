"""Vox Fabula Companion - il compagno di gioco di Vox Fabula per Neverwinter Nights: EE.

Il client che i giocatori aprono: avvia un Mumble tutto suo (portatile, con
impostazioni separate da qualsiasi altro Mumble sul PC), riceve dal server la
posizione dei personaggi e la passa al plugin di portata vc_range; mostra i video
che il server chiede (cinema.py / regia.py). Fino alla 1.2.x si chiamava
"Vox Fabula Voice".
(Il pacchetto Python si chiama ancora nwn_voce: nome interno, non si vede.)
"""

__version__ = "1.3.1.2"
APP_NAME = "Vox Fabula Companion"
# Il server della voce: un nome che punta sempre a casa del server (lo tiene
# aggiornato C:\NWN\scripts\ddns_voice.py quando cambia l'IP). I giocatori
# scrivono solo il loro nome; "Server (avanzato)" nell'ingranaggio lo sostituisce.
DEFAULT_SERVER = "voice.voxfabula.it"
# nomi precedenti dell'app, dal piu' recente: dalla prima cartella che esiste
# recuperiamo le impostazioni (nome, tasto, microfono, Mumble)
OLD_APP_NAMES = ("Vox Fabula Voice", "NWN Voce")
