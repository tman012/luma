"""v210 (PM, Taylor): on test the phone Settings sheet runs full-bleed to the bottom edge of an installed
iPhone PWA (as prod's Settings does): the fixed backdrop reaches one inset past the edge and the bottom
safe-area inset is padding only inside the scroll content. Guard: frontend/tests/safeAreaBottomGuard.test.ts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_settings_sheet_full_bleed():
    t = (ROOT / 'frontend/src/pages/LibrariesPage.tsx').read_text()
    # v266: height via .luma-fullscreen / --luma-screen-h (Home match); no negative-bottom stretch
    assert 'className="luma-fullscreen"' in t
    assert "bottom: 'calc(-1 * var(--luma-bleed))'" not in t
    assert 'data-scroll-content style={{ paddingBottom: SHEET_SCROLL_PAD }}>{content(known.slug)}' in t
    assert "'calc(env(safe-area-inset-bottom) + 24px)'" not in t
    assert "SHEET_SCROLL_PAD = 'calc(var(--luma-end-pad) + 24px)'" in t


def test_guard_present():
    assert (ROOT / 'frontend/tests/safeAreaBottomGuard.test.ts').exists()


def test_sw_v210():
    import re
    v = int(re.search(r"media-kiosk-v(\d+)", (ROOT / 'static/sw.js').read_text()).group(1))
    assert v >= 210                                          # v211 (TV calendar U3) ships on top
