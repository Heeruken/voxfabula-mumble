"""Vox Fabula Voice - voce di prossimita' per Neverwinter Nights: EE.

Il client che i giocatori aprono: avvia un Mumble tutto suo (portatile, con
impostazioni separate da qualsiasi altro Mumble sul PC), riceve dal server la
posizione dei personaggi e la passa al plugin di portata vc_range.
(Il pacchetto Python si chiama ancora nwn_voce: nome interno, non si vede.)
"""

__version__ = "1.2.1"
APP_NAME = "Vox Fabula Voice"
# nome delle versioni di prova precedenti: da li' recuperiamo le impostazioni
OLD_APP_NAME = "NWN Voce"
