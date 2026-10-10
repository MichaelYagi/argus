# PyInstaller spec for the Argus desktop tray app.
# Bundles tray_app.py (the tray icon), the FastAPI server it re-invokes as a
# subprocess (see tray_app.py's own FROZEN/--run-server comments), and the
# Jinja2 templates and static assets into a single standalone executable.
# No Python installation required to run the result.
#
# Must run on the target platform -- Windows produces a Windows build,
# macOS produces a macOS .app. No cross-compile path exists.
#
# Usage (via the build scripts, which set PACKAGE_MODE):
#   packaging\build-windows.ps1    (Windows)
#   packaging/build-macos.sh       (macOS)
#
# Or directly:
#   pyinstaller packaging/argus.spec --distpath dist --workpath build
#
# PACKAGE_MODE env var:
#   onefile (default) -- single executable; slower cold start (re-extracts on
#     each launch), higher antivirus false-positive risk on Windows
#   onedir -- folder (Argus.exe + _internal/ on Windows, or inside Argus.app
#     on macOS); faster start, must keep the folder together
#
# All paths resolve relative to SPECPATH (this file's own directory),
# not the caller's cwd -- a real build confirmed this is necessary.

import os
import sys

ONEFILE = os.environ.get("PACKAGE_MODE", "onefile") == "onefile"

# Read version from the single source of truth
def _read_version():
    init = os.path.join(SPECPATH, "..", "app", "__init__.py")
    for line in open(init).readlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip('"').strip("'")
    return "0.0.0"
version = _read_version()
block_cipher = None
REPO_ROOT = os.path.dirname(SPECPATH)


def repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)


hidden_imports = [
    # pystray selects its OS backend dynamically
    "pystray",
    "pystray._win32",
    "pystray._darwin",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    # uvicorn internals loaded by string at runtime
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # FastAPI / starlette internals
    "starlette.routing",
    "starlette.middleware",
    "starlette.staticfiles",
    "starlette.templating",
    # Argus app entry point and all API routers (FastAPI include_router is
    # dynamic -- PyInstaller's static analysis may not follow it)
    "app.__main__",
    "app.main",
    "app.api.activity",
    "app.api.changes",
    "app.api.clusters",
    "app.api.detect",
    "app.api.enroll",
    "app.api.environments",
    "app.api.export_import",
    "app.api.health",
    "app.api.identities",
    "app.api.images",
    "app.api.jobs",
    "app.api.keys",
    "app.api.logs",
    "app.api.media",
    "app.api.models",
    "app.api.review",
    "app.api.search",
    "app.api.settings",
    "app.api.webhooks",
    "app.core.settings_cache",
    "app.core.paths",
    "app.db.store",
    # dotenv and other runtime deps
    "dotenv",
    "jinja2",
    "jinja2.ext",
    "aiofiles",
    "httpx",
    "pillow_heif",
    "insightface",
    "onnxruntime",
]

# Templates and static files must be bundled -- Jinja2 loads them from disk
# at request time via FileSystemLoader, so they can't be discovered from
# pyc bytecode alone.  The destination paths match the relative layout the
# app already expects (app/templates/, app/static/, app/db/schema.sql), so
# no path-resolution changes are needed in the app itself.
datas = [
    (repo_path("app", "templates"), "app/templates"),
    (repo_path("app", "static"),    "app/static"),
    (repo_path("app", "db", "schema.sql"), "app/db"),
]

# InsightFace package data files (meanshape_68.pkl etc.).
#
# get_object() in pickle_object.py looks up meanshape_68.pkl at runtime.
# The lookup path depends on the InsightFace version installed in the build venv:
#
#   InsightFace >= 1.0.1 (has sys.frozen check):
#     base_dir = sys._MEIPASS  →  _MEIPASS/objects/<name>.pkl
#
#   InsightFace <= 0.7.x (no sys.frozen check, uses __file__):
#     base_dir = Path(__file__).parent  →  _MEIPASS/insightface/data/objects/<name>.pkl
#
# Bundle the objects/ directory at BOTH destinations so the frozen app works
# regardless of which version is installed in the macOS/Windows build venv.
try:
    import insightface.data as _ifd
    _insightface_objects = os.path.join(os.path.dirname(_ifd.__file__), "objects")
    if os.path.isdir(_insightface_objects):
        datas.append((_insightface_objects, "objects"))
        datas.append((_insightface_objects, os.path.join("insightface", "data", "objects")))
except Exception:
    pass

ICON_WIN = repo_path("packaging", "icon.ico")
ICON_MAC = repo_path("packaging", "icon.icns")
icon_path = None
if sys.platform == "win32" and os.path.exists(ICON_WIN):
    icon_path = ICON_WIN
elif sys.platform == "darwin" and os.path.exists(ICON_MAC):
    icon_path = ICON_MAC

a = Analysis(
    [repo_path("packaging", "tray_app.py")],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries + a.zipfiles + a.datas if ONEFILE else [],
    exclude_binaries=not ONEFILE,
    name="Argus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=icon_path,
    runtime_tmpdir=None,
)

if not ONEFILE:
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="Argus",
    )

if sys.platform == "darwin":
    app = BUNDLE(
        exe if ONEFILE else coll,
        name="Argus.app",
        icon=icon_path,
        bundle_identifier="com.argus.app",
        info_plist={
            "CFBundleShortVersionString": version,
            "NSHighResolutionCapable": True,
            "LSUIElement": True,  # tray-only, no Dock icon
        },
    )
