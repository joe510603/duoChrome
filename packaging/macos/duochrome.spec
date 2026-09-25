# PyInstaller spec for duoChrome macOS .app bundle
# Build with: pyinstaller packaging/macos/duochrome.spec
#
# Output: dist/duochrome.app (then wrap into .dmg via build.sh)

# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

block_cipher = None

# PyInstaller sets SPECPATH to the directory containing this .spec file.
# We use it to compute relative paths so the spec works regardless of cwd.
PROJECT_DIR = Path(SPECPATH).resolve().parent.parent  # packaging/macos/ -> project root
ENTRY = PROJECT_DIR / "duochrome" / "cli.py"

a = Analysis(
    [str(ENTRY)],
    pathex=[str(PROJECT_DIR)],
    binaries=[],
    datas=[
        # Bundle the static HTML so the desktop window has something to load
        # even when the dev tree isn't on disk.
        (str(PROJECT_DIR / "duochrome" / "web" / "static" / "index.html"),
         "duochrome/web/static"),
    ],
    hiddenimports=[
        # uvicorn needs these explicitly even though py2app-style auto-import
        # usually catches them; PyInstaller sometimes misses dynamic imports.
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.auto",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Things we don't ship: tkinter (no GUI dep), matplotlib, numpy,
        # pandas — these would balloon the .app for nothing.
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "PIL",
        "pytest",
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
    name="duochrome",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,        # no Terminal pop-up on launch
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,            # add an .icns later if you want a custom icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="duochrome",
)

app = BUNDLE(
    coll,
    name="duochrome.app",
    icon=None,
    bundle_identifier="com.duochrome.app",
    info_plist={
        "CFBundleName": "duoChrome",
        "CFBundleDisplayName": "duoChrome",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "10.15.0",
        "NSHighResolutionCapable": True,
        "LSUIElement": False,  # show in Dock (False = normal app, not menu-bar)
        # Allow network (outbound for proxy / webview server) + file access
        "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
    },
)