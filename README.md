# NWN Voce

Voce di prossimità per **Neverwinter Nights: Enhanced Edition**. Più un personaggio è lontano, più lo senti piano. Chi **sussurra** si sente entro 4 m, chi **parla** entro 12 m, chi **urla** entro 35 m. Personaggi in aree diverse non si sentono.

Questo è il **client**, cioè il programma che aprono i giocatori. Server, relay e bot stanno in un altro progetto (`nwn_mumble`, cartella `server/`).

## Per i giocatori

1. Installa con `NWN-Voce-Setup-<versione>.exe`, oppure scompatta lo zip `…-portatile.zip` dove vuoi.
2. Apri **NWN Voce**, scrivi l'indirizzo del server e il tuo **nome account NWN**, premi **Connetti**.
3. Apri NWN ed entra nel server. Quando sei in gioco la voce diventa **ATTIVA**.

L'app non installa niente nel sistema e non chiede di essere amministratore. Usa un Mumble suo, con impostazioni sue: se usi già Mumble per altro, **non viene né chiuso né modificato**, e i due possono restare aperti insieme. Tutti i dati dell'app stanno in `%APPDATA%\NWN Voce`.

**"Windows ha protetto il PC"**: l'app non è firmata con un certificato a pagamento, quindi al primo avvio Windows lo dice. Clicca **Ulteriori informazioni → Esegui comunque**. Le impronte SHA-256 dei file ufficiali sono nelle note di ogni versione: puoi controllarle su [VirusTotal](https://www.virustotal.com).

## Come funziona

```
server NWN ──(posizioni nel DB)──► relay :27890 ──TCP──► NWN Voce (questo client)
                                                           │  ponte (thread)
                                                           ▼
                                          memoria condivisa "nwn_voice_roster"
                                                           │
                           Mumble portatile ◄── plugin vc_range.dll: regola il
                           (server :64738)       volume di OGNI voce in base a
                                                 distanza, area e sussurra/parla/urla
```

- Le posizioni le scrive il **server** (NWScript). Il client di gioco non viene toccato: niente lettura della memoria di NWN, quindi nessuna patch del gioco lo rompe.
- L'audio posizionale nativo di Mumble è **spento**, perché ronza (bug Mumble #4169). Distanza, portata e pan stereo li fa tutti `vc_range`.
- Il nostro Mumble parte con `mumble.exe -m -c "%APPDATA%\NWN Voce\mumble_settings.json"`: `-c` usa un file di impostazioni separato, `-m` permette di girare accanto a un altro Mumble.

| File | Cosa fa |
|---|---|
| `nwn_voce/main.py` | finestra (pywebview), blocca la seconda istanza, log |
| `nwn_voce/engine.py` | Connetti / Ferma: config Mumble → avvio Mumble → relay → ponte |
| `nwn_voce/mumble_config.py` | scrive le impostazioni del **nostro** Mumble (mai quelle dell'utente) |
| `nwn_voce/relay_client.py` | connessione al relay, si riconnette da solo |
| `nwn_voce/bridge.py` | dal relay alla memoria condivisa letta dal plugin |
| `nwn_voce/net_protocol.py` | protocollo col relay (**non cambiarlo** senza aggiornare il relay) |
| `nwn_voce/winproc.py` | trova e chiude **solo** il nostro `mumble.exe`, per percorso |
| `plugin/vc_range.c` | il plugin Mumble (API ufficiale) |

## Sviluppo

Serve Python 3.10 con `pyinstaller`, `pywebview`, `pythonnet`, più **Inno Setup 6** per l'installer.

```
python -m nwn_voce                          # avvia da sorgente
python -m unittest -v tests.test_nwn_voce   # test (senza Docker, Mumble o NWN)
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

`build.ps1` lancia i test (se falliscono si ferma), costruisce l'app e produce in `dist\` lo zip portatile, l'installer e le loro impronte SHA-256. La versione è **una sola**: `__version__` in `nwn_voce/__init__.py`.

**Mumble portatile** (`vendor/mumble/`, non versionato): è il client Mumble 1.5 (licenza BSD, licenze in `vendor/mumble/licenses/`) con nella cartella `plugins/` solo `link.dll` e `vc_range.dll`. Sono stati tolti i plugin di altri giochi, l'overlay (`mumble_ol*`, che si inietta nei processi dei giochi) e l'helper per le tastiere Logitech G15: non servono, e l'overlay è il tipo di file che gli antivirus guardano male.

**Plugin**: `plugin\build.bat` lo ricompila con Visual Studio 2022. La `vc_range.dll` compilata è versionata, e la build la copia in `vendor\mumble\plugins\`.

## Aggiornamento automatico

All'avvio l'app chiede a GitHub l'ultima **Release** di `Heeruken/voxfabula-mumble`. Se è più nuova, mostra un riquadro con le note della versione e i pulsanti **Aggiorna / Più tardi**. Se l'utente accetta, l'app scarica `NWN-Voce-Setup-<ver>.exe`, ne verifica l'impronta SHA-256 (quella che GitHub calcola da solo per ogni file), si chiude e lancia l'installer in modalità silenziosa. L'installer sostituisce i file e la riapre.

- Si scarica solo via HTTPS e solo da domini GitHub, controllati anche dopo i redirect. Una Release senza impronta viene ignorata; un file che non corrisponde viene buttato.
- Chi usa lo **zip portatile** non si aggiorna da solo: il pulsante apre la pagina della versione nel browser.
- **Versione minima**: se nelle note della Release c'è la riga `Versione minima: 1.2.0`, le app più vecchie mostrano l'aggiornamento come obbligatorio e non si collegano finché non aggiornano. Serve quando cambia il protocollo col relay. La riga non viene mostrata all'utente.

### Pubblicare una versione

1. Alza `__version__` in `nwn_voce/__init__.py` e lancia `packaging\build.ps1`.
2. Su GitHub: **Releases → Draft a new release**. Tag `v<versione>` (per esempio `v1.2.1`), titolo, e nelle note cosa cambia, scritto per i giocatori.
3. Trascina dentro `NWN-Voce-Setup-<versione>.exe` e lo zip portatile, poi **Publish release**. Non spuntare "pre-release": le pre-release vengono ignorate, ed è un modo comodo per provare una versione senza mandarla a tutti.

Da quel momento, chi apre l'app vede l'aggiornamento. GitHub permette 60 controlli l'ora per ogni rete: con un controllo per avvio si resta molto sotto.

## Firma e SmartScreen, senza spendere

Senza certificato l'avviso "editore sconosciuto" resta. Le cose gratuite che lo riducono:

1. **Controlla ogni build su VirusTotal** prima di distribuirla. Se qualche antivirus la segnala, segnala il falso positivo a quel produttore.
2. **Segnala il file a Microsoft** (<https://www.microsoft.com/wdsi/filesubmission>, "Software developer"), così Defender lo impara.
3. **Cambia versione il meno possibile**: SmartScreen costruisce la reputazione **per singolo file**. Ogni nuova build riparte da zero, quindi meglio poche versioni scaricate da tanti che tante versioni scaricate da pochi.
4. **Distribuisci sempre dallo stesso posto**, per esempio le Release di GitHub, e con le impronte SHA-256 in vista.

## Novità della 1.1.0 (rispetto alla 1.0 del giugno 2026)

- Il ponte gira **dentro** l'app: non ci sono più gli exe `nwn-mumble.exe`/`nwn-mumble-relay.exe` che si scompattavano in Temp e partivano nascosti.
- Mumble con **impostazioni e database suoi**: non chiude più il Mumble dell'utente (`taskkill` eliminato) e non ne modifica la configurazione.
- Tolta la modalità "Ospita" (il server ora gira su Docker), insieme alla ricerca dell'IP pubblico.
- Via overlay, helper G15, 50 plugin di altri giochi e PortAudio: 90 MB invece di 113, e solo 2 eseguibili nel pacchetto.
- Installer per utente (Inno Setup), impostazioni e log in `%APPDATA%\NWN Voce`, blocco della seconda istanza, messaggi di stato che dicono cosa non va (relay irraggiungibile, Mumble chiuso…).
- Protocollo col relay **invariato**: chi ha ancora la 1.0 continua a funzionare.
