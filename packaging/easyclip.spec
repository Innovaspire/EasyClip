# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: run from repo root — pyinstaller packaging/easyclip.spec"""
import os
import sys
from pathlib import Path

block_cipher = None

# SPECPATH = directory containing this spec (e.g. .../EasyClip/packaging)
ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
APP_ICO = ROOT / "packaging" / "app.ico"
APP_ICNS = ROOT / "packaging" / "app.icns"

VERSION = os.environ.get("EASYCLIP_VERSION", "0.0.0").lstrip("v")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
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
    icon=str(APP_ICO) if APP_ICO.is_file() else None,
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

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="EasyClip.app",
        icon=str(APP_ICNS) if APP_ICNS.is_file() else None,
        bundle_identifier="com.innovaspire.easyclip",
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        },
    )
