"""Il cinema: riproduce un video a schermo intero sopra Neverwinter Nights.

Gira in un PROCESSO SEPARATO dall'app (``Vox Fabula Companion.exe --video <file>``,
da sorgente ``python -m nwn_voce --video <file>``): se il lettore si pianta o
crasha, la voce non se ne accorge. L'esito torna come codice d'uscita:

    0 = visto fino alla fine     3 = saltato dal giocatore
    1 = errore di riproduzione   2 = file mancante

Come sta sopra il gioco: finestra senza bordi, a tutto schermo e "sempre in
primo piano". Se NWN e' in schermo intero ESCLUSIVO (settings.tml) una finestra
non puo' stargli sopra: allora NWN si riduce a icona prima del video e torna
alla fine. In ogni caso, alla fine, NWN torna in primo piano.

Reti di sicurezza: se il video non parte entro AVVIO_MAX secondi, o dura piu'
della sua durata + MARGINE, la finestra si chiude da sola (esito 1).
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import sys
import threading
import time
from ctypes import wintypes

from . import APP_NAME, paths

log = logging.getLogger("nwn_voce.cinema")

FINE, ERRORE, MANCANTE, SALTATO = 0, 1, 2, 3
AVVIO_MAX = 10.0       # secondi per far partire il video
MARGINE = 15.0         # secondi oltre la durata prima di chiudere comunque
TITOLO = APP_NAME + " - Cinema"

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_u32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.GetWindowThreadProcessId.restype = wintypes.DWORD
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_u32.GetWindow.restype = wintypes.HWND
_u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.SetForegroundWindow.argtypes = [wintypes.HWND]
_u32.GetForegroundWindow.restype = wintypes.HWND
_u32.IsIconic.argtypes = [wintypes.HWND]
_u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_u32.FindWindowW.restype = wintypes.HWND
_u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_u32.BringWindowToTop.argtypes = [wintypes.HWND]
_u32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_void_p]
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                            wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]

SW_MINIMIZE, SW_RESTORE, SW_SHOWNOACTIVATE = 6, 9, 4
GW_OWNER = 4
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002


# ---------------------------------------------------------------- Windows
def _exe_of(hwnd) -> str:
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = _k32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(len(buf))
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        _k32.CloseHandle(h)


def finestra_nwn():
    """La finestra principale di nwmain.exe, o None se il gioco non e' aperto."""
    trovata = []

    def cb(hwnd, _l):
        if _u32.IsWindowVisible(hwnd) and not _u32.GetWindow(hwnd, GW_OWNER) \
                and _exe_of(hwnd) == "nwmain.exe":
            trovata.append(hwnd)
            return False
        return True

    _u32.EnumWindows(_WNDENUMPROC(cb), 0)
    return trovata[0] if trovata else None


def porta_davanti(hwnd) -> bool:
    """Porta in primo piano una finestra anche se non siamo noi quella attiva
    (Windows di norma lo vieta: il "tocco" del tasto Alt sblocca il permesso)."""
    if not hwnd:
        return False
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, SW_RESTORE)
    _u32.keybd_event(VK_MENU, 0, 0, None)
    _u32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, None)
    _u32.BringWindowToTop(hwnd)
    ok = bool(_u32.SetForegroundWindow(hwnd))
    return ok or _u32.GetForegroundWindow() == hwnd


def nwn_esclusivo() -> bool:
    """True se NWN e' impostato in schermo intero esclusivo (settings.tml)."""
    p = os.path.join(os.path.expanduser("~"), "Documents", "Neverwinter Nights", "settings.tml")
    try:
        testo = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    blocco = re.search(r"\[graphics\.window\](.*?)(?=^\[[^\s.]|\Z)", testo, re.S | re.M)
    if not blocco:
        return False
    b = blocco.group(1)
    modo = re.search(r'^\s*mode\s*=\s*"([^"]*)"', b, re.M)
    modo = (modo.group(1) if modo else "windowed").lower()
    senza_bordi = re.search(r"toggle-to-borderless\s*=\s*true", b) is not None
    return modo == "exclusive" or (modo == "fullscreen" and not senza_bordi)


def area_di_gioco(hwnd):
    """(x, y, larghezza, altezza) dell'area di gioco di NWN in pixel veri, o None
    se il gioco e' ridotto a icona o troppo piccolo (allora: tutto lo schermo)."""
    if not hwnd or _u32.IsIconic(hwnd):
        return None
    r = wintypes.RECT()
    if not _u32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wintypes.POINT(0, 0)
    _u32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = r.right - r.left, r.bottom - r.top
    if w < 320 or h < 240:
        return None
    return pt.x, pt.y, w, h


def _adatta(hwnd, rett) -> None:
    """Finestra del video: niente icona nella barra / Alt+Tab, sempre sopra,
    esattamente sull'area di gioco di NWN (se c'e')."""
    stile = _u32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    _u32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (stile | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
    flag = SWP_SHOWWINDOW | SWP_FRAMECHANGED | SWP_NOACTIVATE   # il fuoco lo da' solo porta_davanti
    if rett:
        x, y, w, h = rett
        _u32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, flag)
    else:
        _u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flag | SWP_NOMOVE | SWP_NOSIZE)


def _incolla(hwnd, rett) -> None:
    if not rett:
        return
    r = wintypes.RECT()
    _u32.GetWindowRect(hwnd, ctypes.byref(r))
    if (r.left, r.top, r.right - r.left, r.bottom - r.top) != tuple(rett):
        x, y, w, h = rett
        _u32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE)


_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_u32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_u32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_u32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x20, 0x40
RIPRESA = 0.1          # ogni quanto si guarda se il gioco e' tornato davanti al video
PAUSA_MAX = 180.0      # secondi in pausa (giocatore andato altrove) prima di contarlo saltato


# ---------------------------------------------------------------- pagina
# Il video entra e esce dal nero: il server porta il gioco a nero (FadeToBlack)
# prima di chiederlo e lo riporta su (FadeFromBlack) dopo, cosi' non si vede lo stacco.
PAGINA = """<!doctype html>
<html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#000;overflow:hidden;cursor:none;user-select:none}
video{position:fixed;inset:0;width:100%;height:100%;object-fit:contain;background:#000;
  opacity:0;transition:opacity .7s}
video.su{opacity:1}
.tasto{position:fixed;font:600 15px/1.3 Georgia,serif;color:#d8cfa8;background:rgba(0,0,0,.6);
  border:1px solid rgba(216,207,168,.45);padding:10px 16px;border-radius:4px;letter-spacing:.04em}
#salta{right:32px;bottom:28px;opacity:0;transition:opacity .4s}
body.mossa{cursor:default} body.mossa #salta{opacity:1}
#pausa{left:50%;top:50%;transform:translate(-50%,-50%);text-align:center;display:none;cursor:pointer}
body.ferma{cursor:default} body.ferma #pausa{display:block}
</style></head><body>
<video id="v" playsinline></video>
<div id="salta" class="tasto">Esc per saltare</div>
<div id="pausa" class="tasto">In pausa<br><small>clicca per riprendere &middot; Esc per saltare</small></div>
<script>
const v=document.getElementById('v'), body=document.body, api=()=>window.pywebview.api;
let finito=false, timer=null, avviato=false;
function fine(esito){ if(finito) return; finito=true; try{v.pause()}catch(e){}
  v.classList.remove('su'); setTimeout(()=>api().fine(esito), esito==='fine'?50:600); }
function mostra(){ body.classList.add('mossa'); clearTimeout(timer);
  timer=setTimeout(()=>body.classList.remove('mossa'),2500); }
function ferma(){ if(finito||!avviato||v.paused) return; v.pause(); body.classList.add('ferma'); api().pausa(true); }
function riprendi(){ if(finito||!body.classList.contains('ferma')) return;
  body.classList.remove('ferma'); v.play().catch(()=>{}); api().pausa(false); }
window.addEventListener('pywebviewready',()=>{
  v.src=__VIDEO__;
  v.addEventListener('loadedmetadata',()=>api().durata(v.duration||0));
  v.addEventListener('playing',()=>{ avviato=true; v.classList.add('su'); api().parte(); });
  v.addEventListener('ended',()=>fine('fine'));
  v.addEventListener('error',()=>fine('errore'));
  const p=v.play(); if(p) p.catch(()=>{ v.muted=true; v.play().catch(()=>fine('errore')); });
});
window.addEventListener('blur',ferma);
window.addEventListener('focus',riprendi);
document.addEventListener('keydown',e=>{
  e.preventDefault(); if(e.key==='Escape') fine('saltato'); });
document.addEventListener('mousedown',e=>{ if(!e.target.closest('#pausa')) e.preventDefault(); });
document.addEventListener('contextmenu',e=>e.preventDefault());
document.addEventListener('dragstart',e=>e.preventDefault());
document.addEventListener('mousemove',mostra);
document.getElementById('salta').addEventListener('click',()=>fine('saltato'));
document.getElementById('pausa').addEventListener('click',riprendi);
</script></body></html>
"""


class _Api:
    """Chiamate dalla pagina. Solo attributi privati (vedi main.py)."""

    def __init__(self) -> None:
        self._esito = ERRORE
        self._partito = threading.Event()
        self._durata = 0.0
        self._finestra = None
        self._chiuso = threading.Event()
        self._pausa_da = None          # monotonic d'inizio della pausa in corso
        self._in_pausa = 0.0           # secondi di pausa gia' conclusi
        self._nwn_esclusivo = None     # NWN ridotto a icona (schermo intero esclusivo): torna su prima di chiudere
        self._nwn = None               # la finestra di NWN: torna davanti, SOTTO al lettore, prima che si chiuda

    def parte(self) -> None:
        if not self._partito.is_set():
            log.info("video partito")
        self._partito.set()

    def durata(self, secondi) -> None:
        try:
            self._durata = max(0.0, float(secondi))
        except (TypeError, ValueError):
            pass
        log.info("durata %.1f s", self._durata)

    def pausa(self, si) -> None:
        ora = time.monotonic()
        if si and self._pausa_da is None:
            self._pausa_da = ora
            log.info("in pausa: il giocatore e' andato altrove")
        elif not si and self._pausa_da is not None:
            self._in_pausa += ora - self._pausa_da
            self._pausa_da = None
            log.info("ripreso")

    def fine(self, esito) -> None:
        self._esito = {"fine": FINE, "saltato": SALTATO}.get(esito, ERRORE)
        log.info("fine: %s", esito)
        self._chiudi()

    def _chiudi(self) -> None:
        if self._chiuso.is_set():
            return
        self._chiuso.set()
        nwn = self._nwn or self._nwn_esclusivo
        if nwn:
            # NWN torna su e davanti DIETRO al nero del lettore (sempre in primo piano) e ha il tempo di
            # ridisegnarsi a piena velocita'; solo dopo il lettore sparisce. Senza, in borderless, per un
            # attimo si vedeva il desktop: NWN in secondo piano (o ridotto a icona) non era ancora li'.
            iconic = bool(_u32.IsIconic(nwn))
            if iconic:
                _u32.ShowWindow(nwn, SW_RESTORE)
            davanti = porta_davanti(nwn)
            log.info("chiusura: NWN ridotto a icona=%s, davanti=%s", iconic, davanti)
            time.sleep(0.5 if iconic or self._nwn_esclusivo else 0.3)
        if self._finestra is not None:
            try:
                self._finestra.destroy()
            except Exception:  # noqa: BLE001
                pass


def _cartella_video() -> str:
    d = os.path.join(paths.data_dir(), "video")
    os.makedirs(d, exist_ok=True)
    return d


_SIPARIO = "_sipario_"      # copia in chiaro di un .vfv, solo durante la riproduzione


def riproduci(video: str) -> int:
    """Mostra ``video`` (percorso, o nome dentro la cartella video) e ritorna l'esito.
    Un .vfv (sigillato, vedi sigillo.py) si apre in una copia temporanea accanto, che si
    cancella alla fine; le copie rimaste da un crash si tolgono alla volta dopo."""
    if not os.path.isabs(video):
        video = os.path.join(_cartella_video(), video)
    if not os.path.isfile(video):
        log.warning("video mancante: %s", video)
        return MANCANTE
    cartella = os.path.dirname(video)
    for f in os.listdir(cartella):
        if f.startswith(_SIPARIO):
            try:
                os.remove(os.path.join(cartella, f))
            except OSError:
                pass
    if not video.lower().endswith(".vfv"):
        return _riproduci_file(video)
    from . import sigillo
    try:
        with open(video, "rb") as fh:
            dati, est = sigillo.apri(fh.read())
    except (OSError, ValueError) as exc:
        log.warning("video sigillato illeggibile: %s", exc)
        return ERRORE
    chiaro = os.path.join(cartella, _SIPARIO + os.urandom(4).hex() + est)
    try:
        with open(chiaro, "wb") as fh:
            fh.write(dati)
        del dati
        return _riproduci_file(chiaro)
    finally:
        try:
            os.remove(chiaro)
        except OSError:
            pass


class _Sipario:
    """Una finestra nera semplicissima (Win32, la disegna Windows all'istante) sopra al gioco,
    SENZA prendergli il fuoco. Serve all'avvio del video: NWN (SDL, schermo intero senza bordi) si
    riduce a icona appena il lettore prende il fuoco, e il lettore non e' ancora disegnato (la sua
    pagina non parte finche' non e' mostrata e attiva): senza sipario, per un attimo, il desktop."""

    _WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)

    def __init__(self, rett) -> None:
        self.rett = rett
        self.hwnd = None
        self._pronto = threading.Event()
        self._proc = None

    def apri(self) -> bool:
        threading.Thread(target=self._giro, name="sipario", daemon=True).start()
        return self._pronto.wait(1.0) and bool(self.hwnd)

    def chiudi(self) -> None:
        if self.hwnd:
            _u32.PostMessageW(self.hwnd, 0x0010, 0, 0)          # WM_CLOSE

    def _giro(self) -> None:
        try:
            _u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
            _u32.DefWindowProcW.restype = ctypes.c_ssize_t
            _u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]

            def proc(h, msg, wp, lp):
                if msg == 0x0002:                                   # WM_DESTROY
                    _u32.PostQuitMessage(0)
                    return 0
                if msg == 0x0021:                                   # WM_MOUSEACTIVATE: mai il fuoco
                    return 3                                        # MA_NOACTIVATE
                return _u32.DefWindowProcW(h, msg, wp, lp)
            self._proc = self._WNDPROC(proc)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", self._WNDPROC), ("cbClsExtra", ctypes.c_int),
                            ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                            ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                            ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]
            _k32.GetModuleHandleW.restype = wintypes.HMODULE
            _u32.RegisterClassW.argtypes = [ctypes.c_void_p]
            gdi = ctypes.WinDLL("gdi32")
            gdi.GetStockObject.restype = wintypes.HGDIOBJ
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._proc
            wc.hInstance = _k32.GetModuleHandleW(None)
            wc.hbrBackground = gdi.GetStockObject(4)                # BLACK_BRUSH
            wc.lpszClassName = "VoxFabulaSipario"
            _u32.RegisterClassW(ctypes.byref(wc))                   # gia' registrata: va bene lo stesso
            x, y, w, h = self.rett if self.rett else (0, 0, _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1))
            _u32.CreateWindowExW.restype = wintypes.HWND
            _u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                             ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                             wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
            # TOPMOST | TOOLWINDOW (niente barra/Alt+Tab) | NOACTIVATE; WS_POPUP
            self.hwnd = _u32.CreateWindowExW(0x8 | 0x80 | 0x08000000, "VoxFabulaSipario", "", 0x80000000,
                                             x, y, w, h, None, None, wc.hInstance, None)
            if self.hwnd:
                _u32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
                _u32.UpdateWindow(self.hwnd)
            self._pronto.set()
            msg = wintypes.MSG()
            while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _u32.TranslateMessage(ctypes.byref(msg))
                _u32.DispatchMessageW(ctypes.byref(msg))
        except Exception:  # noqa: BLE001 -- senza sipario si va avanti come prima
            log.exception("sipario")
            self._pronto.set()


def _riproduci_file(video: str) -> int:

    # pixel veri ovunque (schermi al 125-150%): le misure di NWN e le nostre coincidono
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass

    # la pagina sta accanto al video: file:// con percorso relativo, niente server
    from urllib.parse import quote
    pagina = os.path.join(os.path.dirname(video), "_cinema.html")
    with open(pagina, "w", encoding="utf-8") as fh:
        fh.write(PAGINA.replace("__VIDEO__", json.dumps(quote(os.path.basename(video)))))

    # audio subito, senza aspettare un clic
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--autoplay-policy=no-user-gesture-required"

    nwn = finestra_nwn()
    era_davanti = bool(nwn) and _u32.GetForegroundWindow() == nwn
    esclusivo = bool(nwn) and nwn_esclusivo()
    rett = None if esclusivo else area_di_gioco(nwn)
    log.info("NWN %s, davanti=%s, esclusivo=%s, area=%s",
             "aperto" if nwn else "chiuso", era_davanti, esclusivo, rett)
    # il sipario per PRIMA cosa: anche l'avvio del motore del browser puo' rubare il fuoco a NWN
    sipario = None
    if nwn and not esclusivo:
        sipario = _Sipario(rett)
        if sipario.apri():
            log.info("sipario aperto (NWN ridotto a icona=%s)", bool(_u32.IsIconic(nwn)))
        else:
            log.info("sipario non riuscito: avvio come prima")
            sipario = None

    import webview
    api = _Api()
    # in esclusivo NWN si riduce a icona solo DOPO che il nero del lettore copre lo schermo, e torna
    # su PRIMA che il lettore si chiuda: prima, all'inizio e alla fine, si vedeva il desktop
    api._nwn_esclusivo = nwn if esclusivo else None
    api._nwn = nwn
    api._finestra = webview.create_window(TITOLO, pagina, js_api=api, fullscreen=rett is None,
                                          frameless=True, easy_drag=False, on_top=True, hidden=True,
                                          background_color="#000000", focus=True)

    def guardiano():
        # nascosta finche' non e' al suo posto: niente lampi in un angolo dello schermo
        mia = None
        for _ in range(50):
            mia = _u32.FindWindowW(None, TITOLO)
            if mia:
                break
            time.sleep(0.05)
        if mia:
            _adatta(mia, rett)                # al suo posto prima di comparire (niente lampo al centro)
        # NWN (SDL, schermo intero senza bordi) si riduce a icona appena il lettore prende il fuoco, e
        # il lettore non e' ancora disegnato: prima il sipario nero sopra al gioco (senza fuoco), cosi'
        # NWN si riduce DIETRO al nero. Il sipario si toglie quando il video e' sullo schermo.
        log.info("prima di mostrare il lettore: NWN ridotto a icona=%s", bool(nwn and _u32.IsIconic(nwn)))
        api._finestra.show()
        if mia:
            _adatta(mia, rett)
        log.info("in primo piano: %s", porta_davanti(mia))
        if esclusivo:
            _u32.ShowWindow(nwn, SW_MINIMIZE)
        partito = api._partito.wait(AVVIO_MAX)
        if sipario:
            if partito:
                time.sleep(0.25)              # il primo fotogramma del video sopra al sipario
            sipario.chiudi()
            log.info("sipario tolto")
        if not partito:
            log.warning("il video non e' partito entro %s s", AVVIO_MAX)
            api._esito = ERRORE
            api._chiudi()
            return
        inizio = time.monotonic()
        giro = 0
        while not api._chiuso.wait(RIPRESA):
            giro += 1
            # tornati sul gioco durante il video (Win, Alt+Tab, clic sulla barra): il video torna SUBITO
            # sopra e riparte (la pagina riprende da sola quando riprende il fuoco). NWN da solo mai.
            if mia and nwn and _u32.GetForegroundWindow() == nwn:
                if esclusivo:
                    _u32.ShowWindow(nwn, SW_MINIMIZE)
                _u32.SetWindowPos(mia, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)
                porta_davanti(mia)
                log.info("tornati sul gioco durante il video: di nuovo sopra")
            # resta incollata al gioco: se qualcuno la sposta (o sposta NWN) torna al suo posto
            if mia and not esclusivo and giro % 5 == 0:
                _incolla(mia, area_di_gioco(nwn) or rett)
            ora = time.monotonic()
            if api._pausa_da is not None and ora - api._pausa_da > PAUSA_MAX:
                log.info("in pausa da oltre %s s: conta come saltato", PAUSA_MAX)
                api._esito = SALTATO
                api._chiudi()
                return
            pausa = api._in_pausa + (ora - api._pausa_da if api._pausa_da is not None else 0.0)
            if ora - inizio - pausa > (api._durata or 600.0) + MARGINE:
                log.warning("oltre la durata prevista: chiudo")
                api._esito = ERRORE
                api._chiudi()
                return

    webview.start(guardiano, private_mode=True)
    if sipario:
        sipario.chiudi()                      # in ogni caso (gia' chiuso: non fa niente)

    if nwn and (era_davanti or esclusivo):
        log.info("NWN di nuovo davanti: %s", porta_davanti(nwn))
    try:
        os.remove(pagina)
    except OSError:
        pass
    return api._esito


def main_video(argv: list[str]) -> int:
    """Ingresso del processo lettore: ``--video <file>``."""
    logging.basicConfig(
        filename=os.path.join(paths.data_dir(), "cinema.log"), filemode="w",
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    try:
        esito = riproduci(argv[0])
    except Exception:  # noqa: BLE001
        log.exception("errore del lettore")
        esito = ERRORE
    log.info("esito %s", esito)
    print(json.dumps({"esito": esito}), flush=True)
    return esito
