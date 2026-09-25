// vc_range.c -- NWN Voice Range: plugin Mumble.
//
// Attenua OGNI voce in arrivo in base a:
//   - la DISTANZA tra chi parla e me (nel mondo NWN),
//   - la MODALITA' di chi parla (0=sussurra, 1=parla, 2=urla) -> raggio diverso.
// Cosi' "sussurra" si sente solo da vicino, "urla" arriva da lontano: portata
// per-persona, decisa da noi.
//
// Da dove vengono posizioni e modalita': da una MEMORIA CONDIVISA scritta dal
// bridge Python (bridge/shared_state.py). Il callback audio dev'essere veloce,
// quindi qui NON si tocca ne' DB ne' rete: solo una lettura di memoria.
//
// Identita': il callback audio riceve un userID. L'API di Mumble NON va chiamata
// dal thread audio (si bloccherebbe), quindi i nomi (userID->nome Mumble) li
// risolviamo nel MAIN thread e li teniamo in cache; il thread audio fa solo
// lookup. Il nome Mumble si abbina al personaggio NWN per nome.
//
// API UFFICIALE dei plugin Mumble (interfaccia 1.2). Zero reverse engineering.

#define _CRT_SECURE_NO_WARNINGS 1
#define MUMBLE_PLUGIN_NO_DEFAULT_FUNCTION_DEFINITIONS
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

#include "MumblePlugin.h"

// --------------------------- portata (metri NWN) ---------------------------
static const float VC_RADIUS_WHISPER = 4.0f;
static const float VC_RADIUS_TALK    = 12.0f;
static const float VC_RADIUS_SHOUT   = 35.0f;
// Curva volume CONTINUA (niente piattone): volume pieno solo entro NEAR_FRAC del
// raggio, poi decresce con curva dolce fino a 0 esatto al bordo del raggio.
//   gain = ((radius - d) / (radius - near))^VC_CURVE_EXP
// Esempio Parla (12m, near=1.4m): 2m->0.92  4m->0.66  6m->0.43  9m->0.15  12m->0.
static const float VC_NEAR_FRAC  = 0.12f;   // zona "vicino" a volume pieno (12% del raggio)
static const float VC_CURVE_EXP  = 1.5f;    // >1 = cala piu' in fretta sul lungo

// ------------------- memoria condivisa (combacia con shared_state.py) -------------------
#define VCR_TAG   "nwn_voice_roster"
#define VCR_MAGIC 0x52435601u
#define VCR_MAX   64

#pragma pack(push, 1)
typedef struct { uint32_t magic; uint32_t seq; uint32_t count; uint32_t reserved; } vcr_header;
typedef struct { char name[32]; char area[32]; float x, y, z; int32_t mode; } vcr_entry;
#pragma pack(pop)

// ------------------- "chi sta parlando" (memoria condivisa) -------------------
// Mumble stesso ci dice chi trasmette (mumble_onUserTalkingStateChanged): lo
// scriviamo QUI; il RELAY (che CREA questo blocco) lo legge e accende l'icona in
// gioco. Cosi' il "sta parlando" usa la rilevazione di Mumble, sul mic di Mumble,
// per TUTTI -- niente VAD separato lato client.
#define VCT_TAG   "nwn_voice_talk"
#define VCT_MAGIC 0x52435602u
#define VCT_MAX   64
#pragma pack(push, 1)
typedef struct { uint32_t magic; uint32_t seq; uint32_t count; uint32_t reserved; } vct_header;
typedef struct { char name[32]; int32_t talking; } vct_entry;
#pragma pack(pop)

// Log diagnostico DISATTIVO per default (niente file, niente username hardcoded).
// Per attivarlo, imposta la variabile d'ambiente VC_RANGE_LOG = percorso file
// PRIMA di avviare Mumble (es. set VC_RANGE_LOG=%TEMP%\vc_range.log).
static char g_logpath[MAX_PATH] = { 0 };
static bool g_loginit = false;

static mumble_plugin_id_t        g_pluginID = 0;
static struct MumbleAPI_v_1_2_x  g_api;
static bool                      g_haveAPI = false;

static HANDLE       g_map  = NULL;
static const char  *g_view = NULL;        // roster mappato (sola lettura)
static CRITICAL_SECTION g_view_lock;      // protegge g_view tra thread audio e shutdown (audit P2)
static bool             g_lock_ready = false;

static char g_localName[32] = { 0 };      // chi sono io (nome Mumble)

// DEBUG: VC_RANGE_FORCE_PAN=<-1..1> inchioda il pan a un valore fisso (es. 1.0 =
// tutto a destra). Per VERIFICARE la catena stereo end-to-end senza geometria.
static bool  g_hasForcePan = false;
static float g_forcePan = 0.0f;
// DEBUG: VC_RANGE_ZERO_HALF=1 azzera la SECONDA META' del buffer. Discrimina il
// layout reale: interleaved -> audio "a raffica" (meta' di ogni chunk muta);
// planare -> orecchio destro muto; buffer in realta' mono di n campioni -> nessun
// cambiamento udibile (le scritture oltre n cadono nel vuoto).
static bool g_zeroHalf = false;
// DEBUG: VC_RANGE_ZERO_ODD=1 azzera i campioni DISPARI. Verdetto finale sul layout:
// interleaved vero -> orecchio DESTRO completamente MUTO, sinistro pulito;
// buffer in realta' mono -> nessun silenzio, solo volume più basso/timbro ovattato.
static bool g_zeroOdd = false;

// "chi parla": il relay CREA il blocco, qui lo apriamo in SCRITTURA e lo aggiorniamo
// al cambio di stato voce. Solo MAIN thread (i callback talking-state non sono audio).
static HANDLE   g_talkMap  = NULL;
static char    *g_talkView = NULL;
static uint32_t g_talkSeq  = 0;
static struct { char name[32]; int32_t talking; bool used; } g_talk[VCT_MAX];

// userID -> nome (scritta sul MAIN thread, letta dal thread audio)
// gL/gR = guadagno smussato per orecchio sinistro/destro (stereo via pan).
#define VC_CACHE_MAX 128
static struct { mumble_userid_t id; char name[32]; bool used; float gL, gR; } g_cache[VC_CACHE_MAX];

// PAN NEL PLUGIN. MISURATO sul campo (log ch=2 sr=48000): onAudioSourceFetched
// riceve il buffer GIA' in formato output (stereo interleaved) -> il pan per
// orecchio si puo' fare qui, con la stessa smussatura per-campione del volume.
// Il posizionale NATIVO di Mumble resta SPENTO: il suo mixer applica i guadagni
// per-frame SENZA rampa (issue Mumble #4169) ed era la sorgente del ronzio
// metallico direzionale. Qui: un solo sistema, tutto de-zipperato.
// Lo sguardo (avatar_front) arriva da MumbleLink, scritto SMUSSATO dal bridge.
static HANDLE      g_linkMap  = NULL;
static const char *g_linkView = NULL;

static bool link_front(float *fwd_x, float *fwd_y) {
    if (!g_linkView) {
        if (!g_linkMap) g_linkMap = OpenFileMappingA(FILE_MAP_READ, FALSE, "MumbleLink");
        if (g_linkMap) g_linkView = (const char *) MapViewOfFile(g_linkMap, FILE_MAP_READ, 0, 0, 0);
        if (!g_linkView) return false;
    }
    uint32_t ver = *(const uint32_t *) (g_linkView + 0);
    if (ver == 0) return false;                          // link non attivo
    float ax = *(const float *) (g_linkView + 20);       // avatar_front.x = Est  (NWN x)
    float az = *(const float *) (g_linkView + 28);       // avatar_front.z = Nord (NWN y)
    float n = sqrtf(ax * ax + az * az);
    if (n < 0.001f) return false;                        // sguardo verticale/degenere
    *fwd_x = ax / n; *fwd_y = az / n;
    return true;
}

// ------------------------------- utility -------------------------------
static void vc_log(const char *msg) {
    if (!g_loginit) {                       // risolvi il percorso una volta sola
        g_loginit = true;
        const char *env = getenv("VC_RANGE_LOG");
        if (env && env[0]) {
            strncpy(g_logpath, env, sizeof(g_logpath) - 1);
            g_logpath[sizeof(g_logpath) - 1] = 0;
        }
    }
    if (!g_logpath[0]) return;              // var non impostata -> log disattivo
    FILE *f = fopen(g_logpath, "a");
    if (f) { fprintf(f, "%s\n", msg); fclose(f); }
}

static struct MumbleStringWrapper wrap(const char *s) {
    struct MumbleStringWrapper w;
    w.data = s; w.size = strlen(s); w.needsReleasing = false;
    return w;
}

static void cache_put(mumble_userid_t id, const char *name) {
    int i, slot = -1;
    for (i = 0; i < VC_CACHE_MAX; ++i) {
        if (g_cache[i].used && g_cache[i].id == id) {
            strncpy(g_cache[i].name, name, 31); g_cache[i].name[31] = 0; return;
        }
        if (!g_cache[i].used && slot < 0) slot = i;
    }
    if (slot >= 0) {
        g_cache[slot].id = id;
        strncpy(g_cache[slot].name, name, 31); g_cache[slot].name[31] = 0;
        g_cache[slot].used = true;
        g_cache[slot].gL = -1.0f;     // sentinella: guadagni non ancora agganciati
        g_cache[slot].gR = -1.0f;
    }
}

// Copia il nome E (opzionale) restituisce lo slot via out_idx, cosi' il chiamante
// puo' riusare l'indice per il guadagno senza ri-scandire la cache. (audit P3)
static bool cache_get(mumble_userid_t id, char *out /* [32] */, int *out_idx) {
    int i;
    for (i = 0; i < VC_CACHE_MAX; ++i)
        if (g_cache[i].used && g_cache[i].id == id) {
            strncpy(out, g_cache[i].name, 32); out[31] = 0;
            if (out_idx) *out_idx = i;
            return true;
        }
    if (out_idx) *out_idx = -1;
    return false;
}

static void cache_remove(mumble_userid_t id) {
    int i;
    for (i = 0; i < VC_CACHE_MAX; ++i)
        if (g_cache[i].used && g_cache[i].id == id) { g_cache[i].used = false; return; }
}

// risolve il nome via API -- SOLO main thread -- e lo mette in cache
static void cache_resolve(mumble_connection_t conn, mumble_userid_t id) {
    if (!g_haveAPI) return;
    const char *name = NULL;
    if (g_api.getUserName(g_pluginID, conn, id, &name) == MUMBLE_STATUS_OK && name) {
        cache_put(id, name);
        g_api.freeMemory(g_pluginID, name);
    }
}

static void try_open_view(void) {
    if (g_view) return;
    if (!g_map) {
        g_map = OpenFileMappingA(FILE_MAP_READ, FALSE, VCR_TAG);
        if (!g_map) return;
    }
    g_view = (const char *) MapViewOfFile(g_map, FILE_MAP_READ, 0, 0, 0);
}

static bool roster_find(const char *name, vcr_entry *out_e) {
    if (!g_view || !name || !name[0]) return false;
    const vcr_header *h = (const vcr_header *) g_view;
    if (h->magic != VCR_MAGIC) return false;
    const vcr_entry *e = (const vcr_entry *) (g_view + sizeof(vcr_header));
    volatile const uint32_t *pseq = (volatile const uint32_t *) &h->seq;
    int attempt;
    for (attempt = 0; attempt < 5; ++attempt) {     // SEQLOCK: rileggi finche' stabile (audit P2)
        uint32_t s1 = *pseq;
        if (s1 & 1u) continue;                       // scrittore in corso (seq dispari) -> riprova
        uint32_t n = h->count; if (n > VCR_MAX) n = VCR_MAX;
        bool found = false; vcr_entry tmp;
        uint32_t i;
        for (i = 0; i < n; ++i)
            if (_strnicmp(e[i].name, name, 31) == 0) { tmp = e[i]; found = true; break; }
        if (*pseq != s1) continue;                   // cambiato durante la lettura -> riprova
        if (found) *out_e = tmp;
        return found;
    }
    return false;                                    // roster instabile -> stavolta audio normale
}

static float radius_for_mode(int32_t mode) {
    if (mode == 0) return VC_RADIUS_WHISPER;
    if (mode == 2) return VC_RADIUS_SHOUT;
    return VC_RADIUS_TALK;
}

// ---- "chi parla" -> memoria condivisa (scritta qui, letta dal relay) ----
static void talk_open(void) {
    if (g_talkView) return;
    if (!g_talkMap) {
        g_talkMap = OpenFileMappingA(FILE_MAP_WRITE, FALSE, VCT_TAG);   // il relay l'ha creato
        if (!g_talkMap) return;                                         // relay non attivo: riprovo dopo
    }
    g_talkView = (char *) MapViewOfFile(g_talkMap, FILE_MAP_WRITE, 0, 0, 0);
}

static void talk_write(void) {
    if (!g_talkView) { talk_open(); if (!g_talkView) return; }
    vct_header *h = (vct_header *) g_talkView;
    vct_entry  *e = (vct_entry  *) (g_talkView + sizeof(vct_header));
    g_talkSeq = ((g_talkSeq + 1u) | 1u);          // dispari = scrittura in corso (seqlock come roster)
    h->magic = VCT_MAGIC; h->reserved = 0; h->seq = g_talkSeq;
    uint32_t n = 0, i;
    for (i = 0; i < VCT_MAX; ++i) {
        if (!g_talk[i].used) continue;
        strncpy(e[n].name, g_talk[i].name, 31); e[n].name[31] = 0;
        e[n].talking = g_talk[i].talking;
        if (++n >= VCT_MAX) break;
    }
    h->count = n;
    g_talkSeq = (g_talkSeq + 1u);                  // pari = dati stabili
    h->seq = g_talkSeq;
}

static void talk_set(const char *name, int talking) {
    if (!name || !name[0]) return;
    int i, slot = -1;
    for (i = 0; i < VCT_MAX; ++i) {
        if (g_talk[i].used && _strnicmp(g_talk[i].name, name, 31) == 0) {
            if (g_talk[i].talking == talking) return;     // nessun cambiamento
            g_talk[i].talking = talking; talk_write(); return;
        }
        if (!g_talk[i].used && slot < 0) slot = i;
    }
    if (talking && slot >= 0) {                            // nuovo parlante
        strncpy(g_talk[slot].name, name, 31); g_talk[slot].name[31] = 0;
        g_talk[slot].talking = 1; g_talk[slot].used = true; talk_write();
    }
}

// --------------------------- lifecycle / metadata ---------------------------
mumble_error_t mumble_init(mumble_plugin_id_t id) {
    g_pluginID = id;
    memset(g_cache, 0, sizeof(g_cache));
    memset(g_talk, 0, sizeof(g_talk));
    if (!g_lock_ready) { InitializeCriticalSection(&g_view_lock); g_lock_ready = true; }
    {   // debug: pan forzato via env (parsato QUI, main thread; il thread audio legge solo)
        const char *fp = getenv("VC_RANGE_FORCE_PAN");
        if (fp && fp[0]) {
            g_forcePan = (float) atof(fp);
            if (g_forcePan > 1.0f) g_forcePan = 1.0f;
            if (g_forcePan < -1.0f) g_forcePan = -1.0f;
            g_hasForcePan = true;
            vc_log("[vc_range] FORCE_PAN attivo");
        }
        const char *zh = getenv("VC_RANGE_ZERO_HALF");
        if (zh && zh[0] == '1') { g_zeroHalf = true; vc_log("[vc_range] ZERO_HALF attivo"); }
        const char *zo = getenv("VC_RANGE_ZERO_ODD");
        if (zo && zo[0] == '1') { g_zeroOdd = true; vc_log("[vc_range] ZERO_ODD attivo"); }
    }
    vc_log("[vc_range] init");
    return MUMBLE_STATUS_OK;
}

void mumble_shutdown(void) {
    // Aspetta che un eventuale callback audio in corso finisca di leggere g_view
    // PRIMA di smontarlo, altrimenti use-after-free / crash allo spegnimento. (audit P2)
    if (g_lock_ready) EnterCriticalSection(&g_view_lock);
    if (g_view) { UnmapViewOfFile((LPCVOID) g_view); g_view = NULL; }
    if (g_map)  { CloseHandle(g_map); g_map = NULL; }
    if (g_lock_ready) LeaveCriticalSection(&g_view_lock);
    if (g_talkView) { UnmapViewOfFile((LPCVOID) g_talkView); g_talkView = NULL; }
    if (g_talkMap)  { CloseHandle(g_talkMap); g_talkMap = NULL; }
    vc_log("[vc_range] shutdown");
}

struct MumbleStringWrapper mumble_getName(void) { return wrap("NWN Voice Range"); }
mumble_version_t mumble_getAPIVersion(void) { return MUMBLE_PLUGIN_API_VERSION; }

void mumble_registerAPIFunctions(void *apiStruct) {
    memcpy(&g_api, apiStruct, sizeof(g_api));
    g_haveAPI = true;
    vc_log("[vc_range] API registered");
}

void mumble_releaseResource(const void *pointer) { (void) pointer; }
mumble_version_t mumble_getVersion(void) { mumble_version_t v = { 0, 2, 0 }; return v; }
struct MumbleStringWrapper mumble_getAuthor(void) { return wrap("NWN Mumble"); }
struct MumbleStringWrapper mumble_getDescription(void) {
    return wrap("Voce posizionale NWN: portata per-persona (sussurra/parla/urla).");
}
uint32_t mumble_getFeatures(void) { return MUMBLE_FEATURE_AUDIO; }
mumble_version_t mumble_getPluginFunctionsVersion(void) { return MUMBLE_PLUGIN_FUNCTIONS_VERSION; }

// ----------------------------- callbacks (main thread) -----------------------------
void mumble_onServerSynchronized(mumble_connection_t connection) {
    if (!g_haveAPI) return;

    // chi sono io
    mumble_userid_t me = 0;
    if (g_api.getLocalUserID(g_pluginID, connection, &me) == MUMBLE_STATUS_OK) {
        const char *name = NULL;
        if (g_api.getUserName(g_pluginID, connection, me, &name) == MUMBLE_STATUS_OK && name) {
            strncpy(g_localName, name, 31); g_localName[31] = 0;
            cache_put(me, name);
            g_api.freeMemory(g_pluginID, name);
            char buf[128];
            snprintf(buf, sizeof(buf), "[vc_range] io sono '%s' (id=%u)", g_localName, (unsigned) me);
            vc_log(buf);
        }
    }

    // popola la cache con tutti gli utenti gia' presenti
    mumble_userid_t *users = NULL; size_t n = 0;
    if (g_api.getAllUsers(g_pluginID, connection, &users, &n) == MUMBLE_STATUS_OK && users) {
        size_t i; for (i = 0; i < n; ++i) cache_resolve(connection, users[i]);
        g_api.freeMemory(g_pluginID, users);
    }

    // apri la memoria condivisa e logga il roster (prova della pipe, anche da soli)
    try_open_view();
    if (g_view) {
        const vcr_header *h = (const vcr_header *) g_view;
        char buf[128];
        snprintf(buf, sizeof(buf), "[vc_range] roster: magic=%s seq=%u count=%u",
                 (h->magic == VCR_MAGIC ? "ok" : "BAD"), h->seq, h->count);
        vc_log(buf);
        if (h->magic == VCR_MAGIC) {
            uint32_t cnt = h->count; if (cnt > VCR_MAX) cnt = VCR_MAX;
            const vcr_entry *e = (const vcr_entry *) (g_view + sizeof(vcr_header));
            uint32_t i;
            for (i = 0; i < cnt; ++i) {
                char b2[176];
                snprintf(b2, sizeof(b2),
                         "[vc_range]   #%u name='%.31s' area='%.31s' pos=(%.1f,%.1f,%.1f) mode=%d",
                         i, e[i].name, e[i].area, (double) e[i].x, (double) e[i].y, (double) e[i].z,
                         (int) e[i].mode);
                vc_log(b2);
            }
        }
    } else {
        vc_log("[vc_range] roster: memoria condivisa non trovata (writer non attivo?)");
    }
}

void mumble_onUserAdded(mumble_connection_t connection, mumble_userid_t userID) {
    cache_resolve(connection, userID);
}
void mumble_onUserRemoved(mumble_connection_t connection, mumble_userid_t userID) {
    (void) connection;
    char name[32];
    if (cache_get(userID, name, NULL)) talk_set(name, 0);   // spegni la sua icona
    cache_remove(userID);
}

// Mumble ci avvisa quando UN QUALSIASI utente inizia/smette di parlare: e' la
// rilevazione voce di Mumble (sul mic di Mumble). MAIN thread -> posso chiamare l'API.
void mumble_onUserTalkingStateChanged(mumble_connection_t connection, mumble_userid_t userID,
                                      mumble_talking_state_t talkingState) {
    char name[32];
    if (!cache_get(userID, name, NULL)) {        // non ancora in cache -> risolvi ora (main thread)
        cache_resolve(connection, userID);
        if (!cache_get(userID, name, NULL)) return;
    }
    int talking = (talkingState == MUMBLE_TS_TALKING
                || talkingState == MUMBLE_TS_WHISPERING
                || talkingState == MUMBLE_TS_SHOUTING) ? 1 : 0;
    talk_set(name, talking);
}

// --------------------- IL CUORE: attenua per-persona (thread audio) ---------------------
bool mumble_onAudioSourceFetched(float *outputPCM, uint32_t sampleCount, uint16_t channelCount,
                                 uint32_t sampleRate, bool isSpeech, mumble_userid_t userID) {
    if (!isSpeech) return false;

    if (g_zeroHalf) {       // DEBUG layout buffer: azzera la seconda meta' e basta
        uint32_t tot = sampleCount * (uint32_t) channelCount, i;
        for (i = tot / 2; i < tot; ++i) outputPCM[i] = 0.0f;
        return true;
    }
    if (g_zeroOdd) {        // DEBUG layout: azzera i campioni dispari (= canale R se interleaved)
        uint32_t tot = sampleCount * (uint32_t) channelCount, i;
        for (i = 1; i < tot; i += 2) outputPCM[i] = 0.0f;
        return true;
    }

    char sname[32];
    int idx = -1;
    if (!cache_get(userID, sname, &idx)) return false;            // non so chi e' -> normale (no g_view)

    // Lettura del roster (g_view) sotto lock: shutdown non puo' smontarlo mentre
    // leggo. spk/me sono COPIE locali -> il resto dei calcoli e' fuori dal lock. (audit P2)
    vcr_entry spk, me;
    bool found;
    bool locked = g_lock_ready;
    if (locked) EnterCriticalSection(&g_view_lock);
    if (!g_view) try_open_view();
    found = (g_view != NULL) && roster_find(sname, &spk)
            && (g_localName[0] != 0) && roster_find(g_localName, &me);
    if (locked) LeaveCriticalSection(&g_view_lock);
    if (!found) return false;                                     // dati incompleti -> audio normale

    // aree diverse -> non sentire. La separazione tra aree e' garantita SOLO da
    // questo confronto di stringhe (riga sotto): il plugin non imposta alcun
    // context posizionale di Mumble (getFeatures = solo AUDIO, niente
    // fetchPositionalData), quindi non rimuovere questo check. (audit P3)
    if (_strnicmp(spk.area, me.area, 31) != 0) {
        uint32_t t = sampleCount * (uint32_t) channelCount, i;
        for (i = 0; i < t; ++i) outputPCM[i] = 0.0f;
        return true;
    }

    float dx = spk.x - me.x, dy = spk.y - me.y, dz = spk.z - me.z;
    float dist = sqrtf(dx * dx + dy * dy + dz * dz);
    float radius = radius_for_mode(spk.mode);
    float nearr = radius * VC_NEAR_FRAC;

    // Curva CONTINUA: pieno solo nella zona "vicino", poi giu' fino a 0 al bordo.
    float gain;
    if (dist >= radius)      gain = 0.0f;
    else if (dist <= nearr)  gain = 1.0f;
    else                     gain = powf((radius - dist) / (radius - nearr), VC_CURVE_EXP);

    // PAN: dove GUARDO (avatar_front, smussato dal bridge) vs dove sta chi parla.
    // pan in [-1,+1]: +1 = a destra, -1 = a sinistra. Il de-zipper per-orecchio
    // qui sotto smussa anche i salti di pan -> niente gradini -> niente ronzio.
    float pan = 0.0f;
    if (channelCount >= 2) {
        float fwd_x, fwd_y;                          // forward orizzontale nel frame NWN
        if (link_front(&fwd_x, &fwd_y)) {
            float dh = sqrtf(dx * dx + dy * dy);
            if (dh > 0.001f) {
                // "destra" del listener = (fwd_y, -fwd_x) in NWN (x=Est, y=Nord, CCW).
                pan = (dx * fwd_y - dy * fwd_x) / dh;
                if (pan > 1.0f) pan = 1.0f; else if (pan < -1.0f) pan = -1.0f;
            }
        }
    }
    // Curva PERCETTIVA: sqrt(|pan|) alza i pan MEDI (in gioco il PG si gira verso
    // dove cammina -> pan tipico 0.3-0.5, che lineare era ~2dB = impercettibile;
    // con sqrt: 0.45 -> 0.67 -> ~5.5dB, udibile). I laterali pieni restano pieni.
    if (g_hasForcePan) pan = g_forcePan;                  // debug end-to-end
    else pan = (pan >= 0.0f) ? sqrtf(pan) : -sqrtf(-pan);
    // Profondita': l'orecchio LONTANO scende fino al 10% (-20dB) a pan pieno.
    // VERIFICATO sul campo (test ZERO_ODD: canale R muto = stereo end-to-end OK):
    // il pan FUNZIONAVA gia' a 0.7, ma -10dB sull'orecchio lontano era percepito
    // come "entrambe le orecchie". 0.9 rende la direzione inequivocabile.
    const float PAN_DEPTH = 0.9f;
    float targetL = gain * (1.0f - (pan > 0.0f ? pan : 0.0f) * PAN_DEPTH);
    float targetR = gain * (1.0f + (pan < 0.0f ? pan : 0.0f) * PAN_DEPTH);

    // log diagnostico molto diradato (~ ogni 2-3 s mentre qualcuno parla)
    static uint32_t dbg = 0;
    if ((dbg++ % 200u) == 0u) {
        char buf[192];
        snprintf(buf, sizeof(buf),
                 "[vc_range] %s->me dist=%.1f mode=%d gain=%.2f pan=%.2f ch=%u",
                 sname, (double) dist, (int) spk.mode, (double) gain,
                 (double) pan, (unsigned) channelCount);
        vc_log(buf);
    }

    // De-zipper PER-ORECCHIO: smussa L e R campione per campione (one-pole, ~150ms).
    // tau ~150ms perche' le posizioni arrivano a 5Hz: smussa i gradini in una curva
    // continua (niente modulazione a 5Hz = niente ronzio). idx da cache_get. (audit P3/P4)
    float gL = (idx >= 0) ? g_cache[idx].gL : targetL;
    float gR = (idx >= 0) ? g_cache[idx].gR : targetR;
    if (gL < 0.0f) gL = targetL;                     // primo frame: aggancia
    if (gR < 0.0f) gR = targetR;
    if (targetL >= 0.999f && targetR >= 0.999f && gL >= 0.999f && gR >= 0.999f) {
        if (idx >= 0) { g_cache[idx].gL = 1.0f; g_cache[idx].gR = 1.0f; }
        return false;                                // pieno e centrato: nessun costo
    }
    // Fuori raggio e gia' a zero: SILENZIO esatto (niente coda denormale -> niente gracchio).
    if (gain <= 0.0f && gL <= 0.0f && gR <= 0.0f) {
        uint32_t t = sampleCount * (uint32_t) channelCount, i;
        for (i = 0; i < t; ++i) outputPCM[i] = 0.0f;
        if (idx >= 0) { g_cache[idx].gL = 0.0f; g_cache[idx].gR = 0.0f; }
        return true;
    }
    float a = 1.0f - expf(-1.0f / (0.15f * (float) sampleRate));   // costante di tempo ~150 ms
    uint32_t f; uint16_t c;
    for (f = 0; f < sampleCount; ++f) {
        gL += (targetL - gL) * a;
        gR += (targetR - gR) * a;
        if (gain <= 0.0f) {                          // in uscita dal raggio: tronca a zero netto
            if (gL < 0.01f) gL = 0.0f;
            if (gR < 0.01f) gR = 0.0f;
        }
        if (channelCount >= 2) {
            outputPCM[(size_t) f * channelCount + 0] *= gL;   // canale 0 = sinistra
            outputPCM[(size_t) f * channelCount + 1] *= gR;   // canale 1 = destra
            for (c = 2; c < channelCount; ++c)                // eventuali canali extra
                outputPCM[(size_t) f * channelCount + c] *= 0.5f * (gL + gR);
        } else {
            outputPCM[(size_t) f * channelCount] *= gL;       // mono: gL==gR==gain
        }
    }
    if (idx >= 0) { g_cache[idx].gL = gL; g_cache[idx].gR = gR; }
    return true;
}
