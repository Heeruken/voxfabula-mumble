"""Piccole funzioni Windows, senza lanciare altri programmi (niente taskkill,
niente PowerShell): solo chiamate standard a kernel32/user32.

- pids_for_image(path): i processi che girano ESATTAMENTE da quel file .exe.
  Serve a trovare il NOSTRO mumble.exe rimasto aperto da una sessione
  precedente, senza mai toccare un Mumble installato dall'utente.
- close_process(pid): chiede alla finestra di chiudersi (come cliccare la X),
  cosi' Mumble salva le sue impostazioni; solo se non esce entro il tempo
  limite lo si termina.
- single_instance(): impedisce di aprire l'app due volte.
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

log = logging.getLogger(__name__)

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32 = ctypes.WinDLL("user32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WM_CLOSE = 0x0010
ERROR_ALREADY_EXISTS = 183
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32FirstW.restype = wintypes.BOOL
_k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32NextW.restype = wintypes.BOOL
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                            wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.TerminateProcess.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_u32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_u32.EnumWindows.restype = wintypes.BOOL
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.GetWindowThreadProcessId.restype = wintypes.DWORD
_u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_u32.PostMessageW.restype = wintypes.BOOL
_u32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
_u32.MessageBoxW.restype = ctypes.c_int


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _image_path(pid: int) -> str | None:
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        _k32.CloseHandle(h)


def pids_for_image(exe_path: str) -> list[int]:
    """PID dei processi avviati esattamente da ``exe_path``."""
    target = _norm(exe_path)
    name = os.path.basename(exe_path).lower()
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    out = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == name:
                img = _image_path(entry.th32ProcessID)
                if img and _norm(img) == target:
                    out.append(int(entry.th32ProcessID))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return out


def _post_close(pid: int) -> int:
    """Manda WM_CLOSE a tutte le finestre principali del processo."""
    sent = [0]

    def cb(hwnd, _lparam):
        owner = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            _u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            sent[0] += 1
        return True

    _u32.EnumWindows(_WNDENUMPROC(cb), 0)
    return sent[0]


def close_process(pid: int, timeout: float = 6.0) -> bool:
    """Chiude il processo con garbo; lo termina solo se non esce entro ``timeout``.
    True se alla fine il processo non c'e' piu'."""
    h = _k32.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE, False, pid)
    if not h:
        return True  # gia' uscito (o non accessibile: non e' roba nostra)
    try:
        if _post_close(pid):
            if _k32.WaitForSingleObject(h, int(timeout * 1000)) == WAIT_OBJECT_0:
                return True
        log.warning("il processo %s non si e' chiuso da solo: lo termino", pid)
        _k32.TerminateProcess(h, 0)
        return _k32.WaitForSingleObject(h, 3000) == WAIT_OBJECT_0
    finally:
        _k32.CloseHandle(h)


_mutex = None


def single_instance(name: str = "Local\\NWNVoce.SingleInstance") -> bool:
    """True se siamo l'unica istanza dell'app. Il mutex resta aperto finche'
    il processo vive (Windows lo rilascia da solo all'uscita)."""
    global _mutex
    _mutex = _k32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def message_box(text: str, title: str) -> None:
    _u32.MessageBoxW(None, text, title, 0x40)  # MB_ICONINFORMATION
