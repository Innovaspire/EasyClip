# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: run from repo root — pyinstaller packaging/easyclip.spec"""

from pathlib import Path

block_cipher = None

# SPECPATH = directory containing this spec (e.g. .../EasyClip/packaging)
ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
APP_ICON = ROOT / "packaging" / "app.ico"

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(APP_ICON), ".")] if APP_ICON.is_file() else [],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # EasyClip uses QWidget + QtMultimedia; explicitly exclude WebEngine stack.
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebEngineWidgets",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="easyclip",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(APP_ICON) if APP_ICON.is_file() else None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="easyclip",
)
