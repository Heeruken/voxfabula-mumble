# Vox Fabula Companion

Il programma che i giocatori di Vox Fabula tengono aperto accanto a **Neverwinter Nights: Enhanced Edition**. Fino alla 1.2.x si chiamava *Vox Fabula Voice*. Fa due cose:
- **voce di prossimità**;
- **cinema**: i video che il server chiede (l'ingresso in un evento, un finale), mostrati sopra il gioco;
- **palco** (dalla 1.3.1): scene interattive sopra il gioco, per esempio un oggetto in 3D da girare ed esaminare.

**Voce di prossimità.** Più un personaggio è lontano, più lo senti piano. Chi **sussurra** si sente entro 4 m, chi **parla** entro 12 m, chi **urla** entro 35 m. Personaggi in aree diverse non si sentono.

Qui c'è tutto il sistema: l'**app** che aprono i giocatori (`nwn_voce/`, nome interno rimasto dai tempi di "NWN Voce"), il **relay** che gira sul server in Docker (`relay/`) e il **plugin** di Mumble (`plugin/`). Il resto dello stack del server (NWN, server Mumble, bot Discord) sta nel `docker-compose.yml` del server.

## Per i giocatori

1. Installa con `VoxFabula-Companion-Setup-<versione>.exe`, oppure scompatta lo zip `…-portatile.zip` dove vuoi.
2. Apri **Vox Fabula Companion**, scrivi il tuo **nome account NWN**, premi **Connetti**. L'indirizzo non serve: l'app va da sola su `voice.voxfabula.it`.
3. Apri NWN ed entra nel server. Quando sei in gioco la voce diventa **ATTIVA**.

**L'indirizzo della voce.** `voice.voxfabula.it` è un record DNS (Cloudflare, nuvola grigia) che punta all'IP di casa del server. L'IP di casa cambia, e lo tiene aggiornato `C:\NWN\scripts\ddns_voice.py` sul PC del server: un'attività di Windows lo lancia all'accesso e poi ogni 5 minuti, con una chiave Cloudflare che può modificare solo il DNS di voxfabula.it. Il registro è in `%LOCALAPPDATA%\VoxFabula\ddns-voice.log`. Dopo un cambio di IP la voce torna al massimo in 5-6 minuti: il relay si ricollega da solo e rilegge il nome a ogni tentativo. Nell'ingranaggio c'è **Server (avanzato)**, vuoto di base, per le prove (per esempio `127.0.0.1` sul PC del server). L'IP scritto a mano nelle versioni precedenti viene ignorato.

L'app non installa niente nel sistema e non chiede di essere amministratore. Usa un Mumble suo, con impostazioni sue: se usi già Mumble per altro, **non viene né chiuso né modificato**, e i due possono restare aperti insieme. Tutti i dati dell'app stanno in `%APPDATA%\Vox Fabula Companion`: alla prima apertura col nome nuovo, nome, tasto, microfono e Mumble vengono copiati da `%APPDATA%\Vox Fabula Voice`.

Il pulsante **ⓘ** in alto apre **"Cosa fa sul tuo PC"**: tutto quello che il programma fa e non fa, il registro di ogni video mostrato, saltato o scaricato, e l'interruttore **Mostra i video del server**.

**"Windows ha protetto il PC"**: l'app non è firmata con un certificato a pagamento, quindi al primo avvio Windows lo dice. Clicca **Ulteriori informazioni → Esegui comunque**. Le impronte SHA-256 dei file ufficiali sono nelle note di ogni versione: puoi controllarle su [VirusTotal](https://www.virustotal.com).

## Come funziona

```
server NWN ──(posizioni nel DB)──► relay :27890 ──TCP──► Vox Fabula Companion (questo client)
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
- Il nostro Mumble parte con `mumble.exe -m -c "%APPDATA%\Vox Fabula Companion\mumble_settings.json"`: `-c` usa un file di impostazioni separato, `-m` permette di girare accanto a un altro Mumble.
- Il giocatore non deve mai aprire Mumble. L'app ne scrive le impostazioni: push-to-talk (di base **Blocco Maiuscole**, si cambia dall'ingranaggio), niente controlli di aggiornamento di Mumble, niente procedure guidate. A ogni Connetti legge anche il certificato che il server presenta in quel momento e lo segna come accettato, così la domanda "accetti questo certificato?" non compare mai, nemmeno se il certificato del server cambia.

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
| `nwn_voce/regia.py` | richieste di video dal relay → lettore in un processo a parte; registro attività |
| `nwn_voce/cinema.py` | il lettore (`--video <file>`): finestra sopra l'area di gioco di NWN |
| `nwn_voce/videoteca.py` | scarica e verifica i video del server |
| `nwn_voce/sigillo.py` | sigilla e apre i video `.vfv` (niente spoiler) |
| `nwn_voce/palco.py` | scene dal relay → overlay trasparente in un processo a parte (`--palco`) |
| `nwn_voce/web/palco/` | le pagine delle scene (`scene/<tipo>.js`), three.js in `vendor/` (MIT) |

## Cinema: video chiesti dal server

NWN non sa riprodurre un video a metà partita: c'è solo il video d'apertura del modulo, e `EndGame` butta fuori tutto il server. Per questo il video lo mostra il Companion.

```
script del modulo: VF_Cinema(oPC, "ingresso_ossario", "script_dopo")   (vf_cinema_inc.nss)
   │  personaggio fermo e protetto, schermo a nero
   ▼
tabella vf_cinema nel DB "nwn_voice"  ──►  relay  ──►  Companion di QUEL giocatore
                                                          │ regia.py: video presente? interruttore acceso?
                                                          ▼
                                       "Vox Fabula Companion.exe --video <file>"  (processo separato)
                                                          │ visto / saltato / errore ...
   script_dopo, via il nero  ◄──  DB  ◄──  relay  ◄──  esito
```

- **Lettore** (`cinema.py`): una finestra senza bordi, senza icona nella barra, sempre sopra, posata esattamente sull'area di gioco di NWN (in finestra) o su tutto lo schermo. Non si sposta e si salta solo con **Esc**. Se il giocatore va in un'altra finestra il video va in pausa; dopo 3 minuti conta come saltato. Se NWN è in schermo intero **esclusivo** (letto da `settings.tml`), prima si riduce a icona e alla fine torna davanti. Reti di sicurezza: se il video non parte in 10 s, o dura più della sua durata + 15 s, si chiude da solo.
- **Un crash del lettore non tocca la voce**: è un processo a parte, e l'esito torna come codice d'uscita (0 visto, 3 saltato, 2 mancante, 1 errore).
- **Esiti** per lo script: `visto`, `saltato`, `errore`, `mancante` (il video non è sul PC), `disattivato` (il giocatore ha spento i video), `occupato`, più quelli del relay e dello script: `assente` (nessun Companion 1.3+ collegato: dopo 3 s si va avanti), `disconnesso`, `scaduto`, `uscito`. **In ogni caso il personaggio va avanti.**
- **Video**: nomi `a-z 0-9 _ -`. Si pubblicano **sigillati** (`.vfv`, `sigillo.py`): un doppio clic non li apre e su R2 non si guardano, così nessuno si spoilera un evento. La chiave sta nel Companion, ed è voluto: serve contro i curiosi, non contro chi smonta il programma. Il lettore apre il `.vfv` in una copia temporanea che cancella alla fine. Accetta anche `.webm`/`.mp4` in chiaro, utili per le prove. La `videoteca` li scarica a ogni Connetti da `https://nwsync.voxfabula.it/prod/2985642d4b434caab09571e1ec2058f8/cinema/catalogo.json`:
  ```json
  {"video": [{"nome": "ingresso_ossario", "file": "ingresso_ossario.webm", "sha256": "…", "size": 12345678}]}
  ```
  Solo HTTPS e solo quel dominio. Ogni file viene verificato con l'impronta. Si cancellano solo i video scaricati dall'app che spariscono dal catalogo; un catalogo malformato non cancella niente.
- **Lato modulo** (`voxfabula/src/module`): `vf_cinema_inc.nss` (la funzione), `vf_finale.nss` (pannello DM `/finale`: scegli i PG e il video, poi continuano, vanno a un waypoint o escono dal server, solo loro), `vf_finale_poi.nss`. Esempio nel dungeon: `dg_enter.nss`.
- **Attenzione, regola per gli script**: il DB voce è in modalità *delete* (non WAL). Una SELECT letta a metà in una funzione che poi chiama `DelayCommand` resta viva e tiene il DB bloccato, e il relay non riesce più a scrivere. Ogni SELECT va in una funzione a parte, letta fino in fondo.
- **Collaudo**: `python -m unittest tests.test_relay tests.test_cinema` (relay e app); `voxfabula/docs/cinema/collaudo/collaudo_cinema.py` fa la catena intera su nwserver vero con Companion finti. `prove_cinema/prova_completa.py` serve per la prova in partita locale con il lettore vero.

## Palco: scene interattive

Uno script del modulo (`vf_scena_inc.nss`) apre una scena a uno o più giocatori scrivendo in `vf_scena`; il relay la manda al Companion, che la mostra in un overlay **trasparente** sopra l'area di gioco. Gli eventi del giocatore tornano in `vf_scena_ev`, i messaggi dello script vanno in `vf_scena_msg`. Le decisioni (tiri, segreti) le prende sempre lo script. Protocollo in `net_protocol.py`.

- **Overlay pronto**: parte nascosto al Connetti, così la prima scena compare in meno di un secondo; si spegne al Disconnetti e quando si spengono le scene. La finestra sta **fuori schermo** finché non c'è una scena, e la pagina **non si ricarica** mai tra una scena e l'altra (si ripulisce da sola). Il motivo: pywebview 6.x, con `transparent=True` su Windows, mostra e attiva la finestra a ogni navigazione anche se creata `hidden` (`edgechromium.on_navigation_start`); senza queste precauzioni compariva un riquadro grigio, sopra tutto, che rubava il focus a NWN. Un guardiano la rinasconde comunque se compare da sola.
- **Ordine dei messaggi**: quelli che lo script manda subito dopo l'apertura aspettano (nell'app e nella pagina) che la scena sia pronta; poi la pagina manda l'evento `pronta`.
- **Collegamento perso**: il relay chiude le scene aperte ("disconnesso") e il Companion le toglie dallo schermo.
- **Vista condivisa**: chi gira l'oggetto lo fa girare anche agli altri; il relay manda sempre l'ultima posizione, così la posa finale arriva.
- **Cache**: l'overlay tiene il profilo di WebView2 in `%APPDATA%\Vox Fabula Companion\palco_cache\` e serve le pagine sempre da `127.0.0.1:47913` (se occupata, una porta a caso): così JavaScript compilato e shader restano da un avvio all'altro. Contiene solo roba del browser e si può cancellare.
- **Prova della grafica senza Companion**: `web/palco/index.html?prova&oggetto=...` in un browser.

## Sviluppo

Serve Python 3.10 con `pyinstaller`, `pywebview` (6.x: vedi "Palco" per il suo comportamento con le finestre trasparenti), `pythonnet`, più **Inno Setup 6** per l'installer.

```
python -m nwn_voce                          # avvia da sorgente
python -m unittest discover -s tests       # test (senza Docker, Mumble o NWN)
python -m nwn_voce --video <file>          # solo il lettore video, per provarlo
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

`build.ps1` lancia i test (se falliscono si ferma), costruisce l'app e produce in `dist\` lo zip portatile, l'installer e le loro impronte SHA-256. La versione è **una sola**: `__version__` in `nwn_voce/__init__.py`.

**Mumble portatile** (`vendor/mumble/`, non versionato): è il client Mumble 1.5 (licenza BSD, licenze in `vendor/mumble/licenses/`) con nella cartella `plugins/` solo `link.dll` e `vc_range.dll`. Sono stati tolti i plugin di altri giochi, l'overlay (`mumble_ol*`, che si inietta nei processi dei giochi) e l'helper per le tastiere Logitech G15: non servono, e l'overlay è il tipo di file che gli antivirus guardano male.

**Plugin**: `plugin\build.bat` lo ricompila con Visual Studio 2022. La `vc_range.dll` compilata è versionata, e la build la copia in `vendor\mumble\plugins\`.

**Grafica**: presa da voxfabula.it (repo `Heeruken/voxfabula-site`), stile dei capitoli "Mare Astrale". Nell'app ci sono il logo animato (`web/logo-animato.webp`), i font Cinzel e Alegreya (`web/fonts/`, licenza OFL) e lo sfondo astrale, tutto dentro il pacchetto: nessuna richiesta a internet. L'icona e le immagini dell'installer si rigenerano da `packaging/brand/` con `python packaging/make_icon.py`. Quando la finestra è ridotta a icona le animazioni si fermano.

## Il relay (lato server)

Gira nel Docker del server. Legge dal database del gioco dove sta ogni personaggio (lo scrive `vc_voice.nss` nel modulo) e lo manda alle app sulla porta **27890**. Dentro ci sono anche il bot **"chi parla"**, che accende l'icona sopra il personaggio, e la **voce privata DM** ("Appari solo a").

| File | Cosa fa |
|---|---|
| `relay/server.py` | accetta le app, manda posizione e roster; scrive l'icona "chi parla"; inoltra le richieste di video (`vf_cinema`) e ne scrive l'esito |
| `relay/positions.py` | legge `vc_positions`; voce privata DM (`vc_priv_session` / `vc_priv_member`) |
| `relay/talk_listener.py` | bot Mumble che sente chi parla |
| `relay/fantoccio.py` | bot-eco **di test** (solo con `--fantoccio`) |

Il protocollo con le app è **un file solo**, `nwn_voce/net_protocol.py`, usato da entrambi.

```
docker build -f relay/Dockerfile -t nwn-voce-relay .      # dalla radice del progetto
python -m relay --server nwnvoce --port 27890 --db <database> --no-talk   # da sorgente, per prove
```

La build si fa dalla radice, ma il `.dockerignore` lascia passare solo `relay/` e i tre file del protocollo: a Docker arrivano pochi KB. Le versioni sono fissate (`pymumble==1.6.1`, che a sua volta fissa `protobuf` e `opuslib`), quindi la build non cambia da sola nel tempo.

## Aggiornamento automatico

All'avvio l'app chiede a GitHub l'ultima **Release** di `Heeruken/voxfabula-mumble`. Se è più nuova, mostra un riquadro con le note della versione e i pulsanti **Aggiorna / Più tardi**. Se l'utente accetta, l'app scarica `VoxFabula-Companion-Setup-<ver>.exe` (le 1.2.x cercano `VoxFabula-Voice-Setup-<ver>.exe`), ne verifica l'impronta SHA-256 (quella che GitHub calcola da solo per ogni file), si chiude e lancia l'installer in modalità silenziosa. L'installer sostituisce i file e la riapre.

- Si scarica solo via HTTPS e solo da domini GitHub, controllati anche dopo i redirect. Una Release senza impronta viene ignorata; un file che non corrisponde viene buttato.
- Chi usa lo **zip portatile** non si aggiorna da solo: il pulsante apre la pagina della versione nel browser.
- **Versione minima**: se nelle note della Release c'è la riga `Versione minima: 1.2.0`, le app più vecchie mostrano l'aggiornamento come obbligatorio e non si collegano finché non aggiornano. Serve quando cambia il protocollo col relay. La riga non viene mostrata all'utente.

### Pubblicare una versione

1. Alza `__version__` in `nwn_voce/__init__.py` e lancia `packaging\build.ps1`.
2. Su GitHub: **Releases → Draft a new release**. Tag `v<versione>` (per esempio `v1.2.1`), titolo, e nelle note cosa cambia, scritto per i giocatori.
3. Trascina dentro `VoxFabula-Companion-Setup-<versione>.exe`, **`VoxFabula-Voice-Setup-<versione>.exe`** e **`NWN-Voce-Setup-<versione>.exe`**, più lo zip portatile, poi **Publish release**. Sono lo stesso file: i due nomi vecchi servono a chi ha ancora la 1.2.1-1.2.x o la 1.2.0, che cercano solo il loro nome. Non spuntare "pre-release": le pre-release vengono ignorate, ed è un modo comodo per provare una versione senza mandarla a tutti.

Da quel momento, chi apre l'app vede l'aggiornamento. GitHub permette 60 controlli l'ora per ogni rete: con un controllo per avvio si resta molto sotto.

## Firma e SmartScreen, senza spendere

Senza certificato l'avviso "editore sconosciuto" resta. Le cose gratuite che lo riducono:

1. **Controlla ogni build su VirusTotal** prima di distribuirla. Se qualche antivirus la segnala, segnala il falso positivo a quel produttore.
2. **Segnala il file a Microsoft** (<https://www.microsoft.com/wdsi/filesubmission>, "Software developer"), così Defender lo impara.
3. **Cambia versione il meno possibile**: SmartScreen costruisce la reputazione **per singolo file**. Ogni nuova build riparte da zero, quindi meglio poche versioni scaricate da tanti che tante versioni scaricate da pochi.
4. **Distribuisci sempre dallo stesso posto**, per esempio le Release di GitHub, e con le impronte SHA-256 in vista.

## Novità della 1.3.1.1

- **Scene**: niente più riquadro grigio sopra il gioco al Connetti e dopo ogni scena, e il gioco non perde più il primo piano. Le scene si aprono più in fretta dalla seconda volta (cache), si chiudono da sole se cade la connessione, e chi guarda insieme vede l'oggetto fermarsi esattamente dove l'ha lasciato chi lo girava.
- La finestra delle scene resta pronta solo mentre sei collegato e con le scene accese.
- Il relay va aggiornato insieme (vista condivisa, scene chiuse bene se cade la connessione); il protocollo resta compatibile con le 1.3.x.

## Novità della 1.3.1

- **Palco**: scene interattive sopra il gioco (esaminare un oggetto in 3D), con tiri e vista sincronizzati tra i giocatori; interruttore in "Cosa fa sul tuo PC".
- Il server vede chi ha il Companion collegato (`vf_companion`) e quali video esistono (`vf_video`): niente nero inutile se il video non si può mostrare.

## Novità della 1.3.0

- Si chiama **Vox Fabula Companion**. Per Windows resta lo stesso programma (stesso AppId): l'aggiornamento toglie cartella e collegamenti col nome vecchio, e le impostazioni vengono copiate nella cartella nuova.
- **Cinema**: video chiesti dal server, mostrati sopra il gioco; videoteca scaricata e verificata.
- **"Cosa fa sul tuo PC"**: trasparenza su tutto quello che il programma fa, registro attività, interruttore dei video.
- Protocollo col relay **compatibile**: le app vecchie ignorano i messaggi nuovi, e il relay non manda video a chi non dice di saperli mostrare.

## Novità della 1.1.0 (rispetto alla 1.0 del giugno 2026)

- Il ponte gira **dentro** l'app: non ci sono più gli exe `nwn-mumble.exe`/`nwn-mumble-relay.exe` che si scompattavano in Temp e partivano nascosti.
- Mumble con **impostazioni e database suoi**: non chiude più il Mumble dell'utente (`taskkill` eliminato) e non ne modifica la configurazione.
- Tolta la modalità "Ospita" (il server ora gira su Docker), insieme alla ricerca dell'IP pubblico.
- Via overlay, helper G15, 50 plugin di altri giochi e PortAudio: 90 MB invece di 113, e solo 2 eseguibili nel pacchetto.
- Installer per utente (Inno Setup), impostazioni e log in `%APPDATA%\Vox Fabula Voice`, blocco della seconda istanza, messaggi di stato che dicono cosa non va (relay irraggiungibile, Mumble chiuso…).
- Protocollo col relay **invariato**: chi ha ancora la 1.0 continua a funzionare.
