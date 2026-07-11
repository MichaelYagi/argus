"""Sanity checks on templates and static JS to catch authoring hazards."""

from __future__ import annotations

import re
from pathlib import Path

APP_DIR = Path(__file__).parent.parent / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_JS_DIR = APP_DIR / "static" / "js"

# U+201C LEFT DOUBLE QUOTATION MARK, U+201D RIGHT DOUBLE QUOTATION MARK
_SMART_QUOTE_BYTES = (b"\xe2\x80\x9c", b"\xe2\x80\x9d")


def _frontend_files():
    files = list(TEMPLATES_DIR.rglob("*.html"))
    if STATIC_JS_DIR.exists():
        files += list(STATIC_JS_DIR.rglob("*.js"))
    return files


def test_no_smart_quotes_in_templates_or_js():
    """Smart-quote characters in HTML/JS string literals produce mangled URLs."""
    offenders = []
    for path in _frontend_files():
        raw = path.read_bytes()
        hits = sum(raw.count(sq) for sq in _SMART_QUOTE_BYTES)
        if hits:
            offenders.append(f"{path.relative_to(APP_DIR.parent)}: {hits} smart quote(s)")
    assert not offenders, "Smart quotes found — use ASCII double-quotes in HTML/JS:\n" + "\n".join(offenders)


def test_esc_escapes_ascii_double_quote():
    """The _esc() JS helper in base.html must replace ASCII \" (U+0022), not a smart quote."""
    base = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    # Extract the _esc function body
    m = re.search(r"function _esc\(s\)\s*\{([^}]+)\}", base)
    assert m, "_esc function not found in base.html"
    body = m.group(1)
    # Must contain .replace(/"/g, with an ASCII double-quote inside the regex literal
    assert '.replace(/"/' in body, (
        '_esc() does not replace ASCII double-quote (U+0022); '
        'check for smart-quote regression in the replace regex'
    )
