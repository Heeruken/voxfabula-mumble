# -*- mode: python ; coding: utf-8 -*-
# Vox Fabula Voice - build a CARTELLA (onedir). Non usare onefile: si riscompatta in
# Temp a ogni avvio (lento, e gli antivirus lo guardano male).
# Uscita: dist\Vox Fabula Voice\  ->  "Vox Fabula Voice.exe" + "_internal\" (web, mumble, python).
# Lanciare da packaging\build.ps1, non a mano.
import os
import re

# Python 3.10.0 ha un bug nel modulo 'dis' che manda in crash PyInstaller
# (IndexError in _get_const_info, corretto nella 3.10.1).
import dis as _dis
_orig_gci = _dis._get_const_info
def _safe_gci(const_index, const_list):
    try:
        return _orig_gci(const_index, const_list)
    except IndexError:
        return None, 'None'
_dis._get_const_info = _safe_gci

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(SPECPATH)
PKG = os.path.join(ROOT, 'nwn_voce')
BUILD = os.path.join(ROOT, 'build')

# ---- versione: UNA sola fonte, nwn_voce/__init__.py ----
with open(os.path.join(PKG, '__init__.py'), encoding='utf-8') as fh:
    VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', fh.read()).group(1)
nums = [int(x) for x in re.findall(r'\d+', VERSION)][:4]
nums += [0] * (4 - len(nums))
os.makedirs(BUILD, exist_ok=True)
VERFILE = os.path.join(BUILD, 'version_info.txt')
with open(os.path.join(SPECPATH, 'version_info.template'), encoding='utf-8') as fh:
    tpl = fh.read()
with open(VERFILE, 'w', encoding='utf-8') as fh:
    fh.write(tpl.replace('{T}', repr(tuple(nums))).replace('{V}', '.'.join(map(str, nums))))

datas = [
    (os.path.join(PKG, 'web'), 'web'),
    (os.path.join(ROOT, 'vendor', 'mumble'), 'mumble'),
]
binaries = []
hiddenimports = ['webview.platforms.winforms', 'clr']

for pkg in ('webview', 'pythonnet', 'clr_loader'):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(PKG, '__main__.py')],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'qtpy', 'sounddevice',
              'matplotlib', 'numpy', 'pandas', 'gi', 'cefpython3', 'unittest', 'pydoc',
              'webview.platforms.android', 'webview.platforms.cocoa',
              'webview.platforms.gtk', 'webview.platforms.qt', 'webview.platforms.cef'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Vox Fabula Voice',
    debug=False,
    strip=False,
    upx=False,          # UPX = classico falso positivo degli antivirus: mai
    console=False,
    icon=os.path.join(SPECPATH, 'icon.ico'),
    version=VERFILE,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Vox Fabula Voice')
