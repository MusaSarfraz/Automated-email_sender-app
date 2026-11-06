# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for EmailSender (--onedir --windowed).

Build from the project root:

    pyinstaller EmailSender.spec

Produces dist/EmailSender/EmailSender.exe plus its support files.
"""

import os

from PyInstaller.utils.hooks import collect_submodules

root = SPECPATH
assets = os.path.join(root, "email_sender", "assets")
icon_path = os.path.join(assets, "EmailSender.ico")

# Bundle the icon and (if the admin dropped one in) the Google service-account
# key, both served from email_sender/assets/ at runtime.
datas = []
if os.path.exists(icon_path):
    datas.append((icon_path, "email_sender/assets"))
sa_key = os.path.join(assets, "service_account.json")
if os.path.exists(sa_key):
    datas.append((sa_key, "email_sender/assets"))

# keyring's Windows Credential Manager backend and email-validator's DNS dep are
# imported lazily, so name them explicitly.
hiddenimports = [
    "keyring.backends.Windows",
    "keyring.backends.chainer",
    "win32ctypes.core",
    "win32ctypes.core.ctypes",
    "dns",
    "dns.resolver",
]
hiddenimports += collect_submodules("win32ctypes")

a = Analysis(
    [os.path.join(root, "EmailSender_launcher.py")],
    pathex=[root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtQml", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EmailSender",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX off: fewer antivirus false positives
    console=False,             # --windowed: no console window
    icon=icon_path if os.path.exists(icon_path) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="EmailSender",        # dist/EmailSender/
)
