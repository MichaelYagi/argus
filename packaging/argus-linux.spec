# PyInstaller spec for the Argus Linux CLI build.
# Produces a standalone `argus` binary that starts the server -- same as
# `python -m app` but with no Python installation required.
# No tray icon: pystray has no reliable single tray protocol across Linux
# desktop environments (GNOME, KDE, Xfce all behave differently), so this
# build is a plain CLI/server binary only.
#
# The resulting binary is tied to the glibc version of the build runner --
# the same limitation as any compiled Linux binary.
#
# Usage (via build-linux.sh, or directly):
#   pyinstaller packaging/argus-linux.spec --distpath dist --workpath build

import os

block_cipher = None
REPO_ROOT = os.path.dirname(SPECPATH)


def repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)


hidden_imports = [
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
    "starlette.routing",
    "starlette.middleware",
    "starlette.staticfiles",
    "starlette.templating",
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
    "dotenv",
    "jinja2",
    "jinja2.ext",
    "aiofiles",
    "httpx",
    "pillow_heif",
    "insightface",
    "onnxruntime",
]

datas = [
    (repo_path("app", "templates"), "app/templates"),
    (repo_path("app", "static"),    "app/static"),
    (repo_path("app", "db", "schema.sql"), "app/db"),
]

a = Analysis(
    [repo_path("packaging", "server_main.py")],
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
    a.binaries,
    a.zipfiles,
    a.datas,
    name="argus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # CLI binary -- server logs go to stdout/stderr
    onefile=True,
)
