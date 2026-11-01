# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [("static", "static")]
binaries = []
hiddenimports = ["extract"]
for pkg in ("webview", "pythonnet", "clr_loader", "bottle", "proxy_tools"):
    try:
        extra_datas, extra_binaries, extra_hidden = collect_all(pkg)
    except Exception:
        continue
    datas += extra_datas
    binaries += extra_binaries
    hiddenimports += extra_hidden

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="火星文",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="火星文",
)
