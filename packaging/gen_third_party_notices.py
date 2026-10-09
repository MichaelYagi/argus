#!/usr/bin/env python3
"""Writes THIRD-PARTY-NOTICES.txt for a desktop build: every third-party
package the packaged app actually contains, with its name, license, and the
full license text the package ships with.

Run it with the build venv's Python (so importlib.metadata sees exactly the
packages that went into that build):

    .venv/bin/python packaging/gen_third_party_notices.py dist/THIRD-PARTY-NOTICES.txt

Why this exists: MIT/BSD/Apache-style licenses require their copyright notice
and license text to accompany copies -- including compiled copies -- and some
components are LGPL, which adds its own notice requirements. This generates
the file from installed package metadata rather than a hand-maintained list
that would rot when dependencies change.
"""
from __future__ import annotations

import importlib.metadata as md
import re
import sys
import sysconfig
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

NOT_BUNDLED = {
    "pip", "setuptools", "wheel", "pkg-resources", "pyinstaller-hooks-contrib",
    "altgraph", "macholib", "pefile", "pywin32-ctypes",
}

LICENSE_FILE_RE = re.compile(r"(^|/)(licen[sc]e|copying|notice|copyright)[^/]*$", re.I)

HEADER = """\
THIRD-PARTY NOTICES

This application (Argus) is free software, licensed under the MIT License;
the full text is in LICENSE.txt, next to this file. It bundles the
third-party software listed below. Each component is distributed under its
own license; the license text each package ships with is reproduced in full.

Source code
-----------
The complete corresponding source code for this application, and the build
scripts used to produce this package, are available at
https://github.com/MichaelYagi/argus, at the tag matching the version shown
in the app. If you received this build and cannot reach that address, ask the
person you received it from for the source.

LGPL-licensed components
------------------------
Any component marked LGPL below (for example pystray) is used as an
unmodified separate library, under the terms of its license. Its source is
available from its project page (linked in its entry below) and from PyPI.

PyInstaller
-----------
This application is packaged with PyInstaller. Its bootloader is included in
the executable, under the GNU GPL v2 or later with a special exception that
allows it to be used to distribute applications under any license. See
https://pyinstaller.org/en/stable/license.html

Python runtime
--------------
The executable includes the CPython runtime (PSF License,
https://docs.python.org/3/license.html).
"""


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def license_label(meta) -> str:
    lic = meta.get("License-Expression") or ""
    if not lic:
        raw = (meta.get("License") or "").strip()
        lic = raw if raw and len(raw) < 80 and "\n" not in raw else ""
    if not lic:
        cls = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
        lic = "; ".join(cls)
    return lic or "(not declared -- see license text below or the project page)"


def project_url(meta) -> str:
    home = meta.get("Home-page")
    if home:
        return home
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in {"homepage", "home", "source", "repository", "source code"} and url.strip():
            return url.strip()
    for entry in meta.get_all("Project-URL") or []:
        _, _, url = entry.partition(",")
        if url.strip():
            return url.strip()
    return ""


def python_entries() -> list[dict]:
    out = []
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name or norm(name) in NOT_BUNDLED:
            continue
        texts = []
        for f in dist.files or []:
            if LICENSE_FILE_RE.search(str(f).replace("\\", "/")):
                t = read_text(Path(dist.locate_file(f)))
                if t and t not in texts:
                    texts.append(t)
        out.append({
            "name": name, "version": dist.version, "license": license_label(dist.metadata),
            "url": project_url(dist.metadata), "texts": texts,
        })
    return sorted(out, key=lambda e: e["name"].lower())


def python_runtime_license() -> str:
    for base in (Path(sysconfig.get_paths()["stdlib"]), Path(sys.base_prefix)):
        for name in ("LICENSE.txt", "LICENSE"):
            t = read_text(base / name)
            if t:
                return t
    return ""


def render(entries: list[dict], title: str) -> str:
    parts = [f"\n{'=' * 78}\n{title}\n{'=' * 78}\n"]
    for e in entries:
        parts.append(f"\n--- {e['name']} {e['version']} ---\nLicense: {e['license']}")
        if e["url"]:
            parts.append(f"Project: {e['url']}")
        if e["texts"]:
            for t in e["texts"]:
                parts.append("\n" + t + "\n")
        else:
            parts.append("(this package ships no license file; the license above is what it declares)\n")
    return "\n".join(parts)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: gen_third_party_notices.py <output-file>", file=sys.stderr)
        return 2
    py = python_entries()
    out = [HEADER]
    out.append(render(py, f"Python packages ({len(py)})"))
    rt = python_runtime_license()
    if rt:
        out.append(f"\n{'=' * 78}\nPython runtime license (PSF)\n{'=' * 78}\n\n{rt}\n")
    dest = Path(sys.argv[1])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(out), encoding="utf-8")
    missing = [e["name"] for e in py if not e["texts"]]
    print(f"wrote {dest}: {len(py)} packages, {dest.stat().st_size // 1024} KB")
    if missing:
        print(f"note: no license file shipped by: {', '.join(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
