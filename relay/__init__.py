"""Relay di NWN Voce: la parte del sistema voce che gira sul server (Docker).

- server.py         accetta le app dei giocatori (porta 27890) e manda a ognuna
                    la sua posizione + il roster di tutti, letti dal database
                    che scrive vc_voice.nss nel modulo
- positions.py      lettura del database (vc_positions) e voce privata DM
                    ("Appari solo a": vc_priv_session / vc_priv_member)
- talk_listener.py  bot Mumble "chi parla": accende l'icona in gioco
- fantoccio.py      bot-eco di TEST (solo con --fantoccio)

Il protocollo con le app e' nwn_voce/net_protocol.py: UN file solo, condiviso.
"""
