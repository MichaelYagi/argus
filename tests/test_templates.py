"""Sanity checks on templates and static JS to catch authoring hazards."""

from __future__ import annotations

import re
from pathlib import Path

APP_DIR = Path(__file__).parent.parent / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_JS_DIR = APP_DIR / "static" / "js"
STATIC_CSS_DIR = APP_DIR / "static" / "css"

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


# ---------------------------------------------------------------------------
# Android GPU compositor layer guards (tag page)
# These invariants were hard-won: breaking them makes .t-box elements invisible
# on real Android Chrome due to compositor layer drops. Do not remove.
# ---------------------------------------------------------------------------

def _tag_html() -> str:
    return (TEMPLATES_DIR / "tag.html").read_text(encoding="utf-8")


def _style_css() -> str:
    return (STATIC_CSS_DIR / "style.css").read_text(encoding="utf-8")


def test_tag_wrap_has_compositor_layer():
    """#tag-wrap must have transform: translateZ(0) to keep it on a GPU compositor layer.

    Without this, Android Chrome drops absolutely-positioned .t-box children during
    scroll/repaint, making bboxes invisible.
    """
    assert "transform: translateZ(0)" in _style_css(), (
        "#tag-wrap is missing transform: translateZ(0) in style.css — "
        "required to prevent Android Chrome from dropping .t-box compositor layers"
    )


def test_tbox_has_will_change_transform():
    """.t-box elements must each be on their own GPU layer via will-change: transform.

    Without this, Android Chrome flickers or drops the bbox overlays when the
    address bar slides in/out.
    """
    assert "will-change: transform" in _style_css(), (
        ".t-box is missing will-change: transform in style.css — "
        "required to prevent Android Chrome bbox flicker"
    )


def test_no_passive_false_touch_listener_in_tag_html():
    """{ passive: false } touch/pointer listeners on #tag-wrap break Android compositor.

    This was the original root cause of invisible bboxes on Android. Must not return.
    """
    tag = _tag_html()
    assert "passive: false" not in tag, (
        "tag.html contains '{ passive: false }' — this breaks the Android GPU compositor "
        "for absolutely-positioned children of #tag-wrap"
    )


def test_no_draggable_false_on_tag_photo():
    """draggable='false' on #tag-photo breaks the Android GPU compositor for .t-box children.

    Setting this attribute causes the same compositor layer drop as passive: false.
    Use document.addEventListener('dragstart') inside draw-mode IIFE instead.
    """
    tag = _tag_html()
    img_match = re.search(r'<img\b[^>]*\bid=["\']tag-photo["\'][^>]*>', tag)
    assert img_match, "Could not find <img id='tag-photo'> in tag.html"
    assert "draggable" not in img_match.group(0), (
        "#tag-photo has a draggable attribute — this breaks the Android compositor; "
        "remove it and use the document dragstart listener inside the draw-mode IIFE"
    )


def test_dragstart_listener_is_on_document():
    """Desktop image-drag prevention must use document.addEventListener('dragstart').

    Attaching dragstart to #tag-photo or #tag-wrap breaks the Android compositor.
    The fix is a document-level listener that's only active during draw mode.
    """
    tag = _tag_html()
    assert "document.addEventListener('dragstart'" in tag, (
        "tag.html is missing document.addEventListener('dragstart') — "
        "click-drag on desktop will drag the whole image instead of drawing a bbox"
    )


def test_renderbboxes_has_raf_retry():
    """renderBoxes() must retry via requestAnimationFrame when clientWidth is 0.

    On slow Android hardware, img.clientWidth is 0 when onload fires. Without the
    rAF retry the bboxes are rendered at 0x0 and are invisible.
    """
    tag = _tag_html()
    assert "requestAnimationFrame(renderBoxes)" in tag, (
        "renderBoxes() is missing the rAF retry guard — bboxes will be 0x0 on slow Android hardware"
    )
