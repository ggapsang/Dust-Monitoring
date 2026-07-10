"""Static checks against the admin page assets to enforce
refs/design_guideline.md (Win9x/2000 classic style).

Mirrors the spirit of SocketDaim's `test_auto_html_design.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ADMIN_DIR = Path(__file__).resolve().parents[1] / "src" / "decision_agent" / "admin"
_INDEX_HTML = _ADMIN_DIR / "templates" / "index.html"
_CSS_FILE = _ADMIN_DIR / "static" / "css" / "admin.css"
_JS_FILE = _ADMIN_DIR / "static" / "js" / "admin.js"


def _read(path: Path) -> str:
    assert path.exists(), f"missing: {path}"
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------- forbidden

def test_css_has_no_positive_border_radius() -> None:
    css = _read(_CSS_FILE)
    # any border-radius with a non-zero value
    matches = re.findall(r"border-radius\s*:\s*([^;]+);", css)
    for v in matches:
        assert re.match(r"^\s*0(?:px|%)?\s*$", v), f"border-radius must be 0 (got: {v!r})"


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def test_css_has_no_box_shadow() -> None:
    css = _strip_comments(_read(_CSS_FILE))
    assert re.search(r"\bbox-shadow\s*:", css) is None, "box-shadow is forbidden"


def test_css_has_no_gradients() -> None:
    css = _strip_comments(_read(_CSS_FILE))
    assert "linear-gradient" not in css
    assert "radial-gradient" not in css


def test_css_has_no_transitions_or_animations() -> None:
    css = _strip_comments(_read(_CSS_FILE))
    assert re.search(r"\btransition\s*:", css) is None, "transitions are forbidden"
    assert re.search(r"\banimation\s*:", css) is None, "animations are forbidden"
    assert "@keyframes" not in css


# --------------------------------------------------------------------- required tokens

@pytest.mark.parametrize("color", ["#ffffff", "#000000", "#a00000"])
def test_css_uses_core_palette(color: str) -> None:
    css = _read(_CSS_FILE).lower()
    assert color.lower() in css, f"missing core palette color: {color}"


def test_css_uses_classic_fonts() -> None:
    css = _read(_CSS_FILE)
    assert "MS Sans Serif" in css
    assert "Tahoma" in css
    assert "Courier New" in css


# --------------------------------------------------------------------- required structure

def test_html_imports_admin_css() -> None:
    html = _read(_INDEX_HTML)
    assert '/admin/static/css/admin.css' in html


def test_html_uses_5_area_grid() -> None:
    html = _read(_INDEX_HTML)
    for area in ("topnav", "side", "main", "status"):
        assert f'class="{area}"' in html or f"class='{area}'" in html, f"missing area: {area}"


def test_html_has_three_panels() -> None:
    html = _read(_INDEX_HTML)
    # role_mapping + alarm_mapping panels in side, decisions in main
    assert "role_mapping" in html
    assert "alarm_mapping" in html
    assert 'id="tbl-decisions"' in html


def test_html_has_three_tabs() -> None:
    html = _read(_INDEX_HTML)
    for tab in ("recent", "pending", "stuck"):
        assert f'data-tab="{tab}"' in html


def test_html_has_required_modals() -> None:
    html = _read(_INDEX_HTML)
    for mid in ("modal-role", "modal-alarm", "modal-force"):
        assert f'id="{mid}"' in html


def test_html_uses_judgment_badge_class() -> None:
    # judgment-badge is the standard class per design_guideline.md §5.2.
    js = _read(_JS_FILE)
    assert "judgment-badge" in js


def test_html_has_reload_buttons() -> None:
    html = _read(_INDEX_HTML)
    assert 'id="btn-reload-roles"' in html
    assert 'id="btn-reload-alarms"' in html


# --------------------------------------------------------------------- no emoji

def test_html_has_no_emoji() -> None:
    html = _read(_INDEX_HTML)
    # crude but effective: any char in the supplemental planes / common emoji blocks
    for ch in html:
        cp = ord(ch)
        assert not (0x1F300 <= cp <= 0x1FAFF), f"emoji detected: {ch}"
        assert not (0x2600 <= cp <= 0x27BF), f"dingbat/symbol detected: {ch}"


def test_css_has_no_emoji() -> None:
    css = _read(_CSS_FILE)
    for ch in css:
        cp = ord(ch)
        assert not (0x1F300 <= cp <= 0x1FAFF), f"emoji in css: {ch}"
