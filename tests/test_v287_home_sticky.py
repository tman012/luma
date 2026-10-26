"""v287: phone Home sticky header — document scroll restored after stale locks; SW v287."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sw_is_v287_or_newer():
    sw = (ROOT / 'static' / 'sw.js').read_text()
    stamp = (ROOT / 'static' / 'sw-stamp.json').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 287
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp


def test_phone_header_is_fixed_outside_ptr_shell():
    """Fixed TopBar is a sibling of the PTR translateY shell (prod AuthHome pattern)."""
    src = (ROOT / 'frontend/src/pages/MinimalHomePage.tsx').read_text()
    assert "position: 'fixed'" in src
    assert 'data-testid="phone-header"' in src
    # Header must not carry data-shift-root (filter/transform transition on the
    # fixed bar). Content shift-root stays for the account panel.
    assert 'data-testid="phone-header"' in src
    header_line = [ln for ln in src.splitlines() if 'data-testid="phone-header"' in ln][0]
    assert 'data-shift-root' not in header_line
    assert 'data-shift-root' in src  # content still shifts
    assert 'useBodyScroll(!kiosk)' in src
    assert 'usePullToRefresh(handleHomeRefresh, !kiosk && ready)' in src
    assert "transform: pullPx > 0 ? `translateY(${pullPx}px)` : undefined" in src


def test_clear_stale_lock_restores_document_scroll():
    """Stale overflow:hidden must become auto, not '' (CSS overflow:hidden would re-lock)."""
    ptr = (ROOT / 'frontend/src/hooks/usePullToRefresh.ts').read_text()
    assert "doc.body.style.overflow = 'auto'" in ptr
    assert "doc.documentElement.style.overflow = 'auto'" in ptr
    # Must not clear back to empty inline style.
    assert "doc.body.style.overflow = ''" not in ptr
    assert "doc.documentElement.style.overflow = ''" not in ptr


def test_use_body_scroll_reasserts_on_resume():
    body = (ROOT / 'frontend/src/hooks/useBodyScroll.ts').read_text()
    assert "visibilitychange" in body
    assert "overflow = 'auto'" in body
