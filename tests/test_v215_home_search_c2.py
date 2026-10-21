"""v215 C2: desktop Home search pill + centered palette (SPEC-home-search-C2.md)."""
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / 'frontend' / 'src'
HOME = (SRC / 'pages' / 'MinimalHomePage.tsx').read_text()
PILL = (SRC / 'components' / 'search' / 'HomeSearchPill.tsx').read_text()
PAL = (SRC / 'components' / 'search' / 'HomeSearchPalette.tsx').read_text()
HOST = (SRC / 'components' / 'search' / 'MinimalSearchHost.tsx').read_text()


def test_kiosk_header_is_logo_and_avatar_only():
    kiosk = HOME.split('if (kiosk) {', 1)[1].split('return (\n    <div style={page}', 1)[0]
    header = kiosk.split('<header', 1)[1].split('</header>', 1)[0]
    assert 'kiosk-logo' in header and 'menu(48)' in header
    assert 'button' not in header and 'kioskSearchBtn' not in HOME and 'headLeft' not in HOME
    # v227 R3: the kiosk Home's search is the Search title and field at the top (the pill left Home)
    assert '<HomeSearchR3 />' in kiosk and '<HomeSearchPill />' not in kiosk


def test_phone_branch_keeps_round_search_button():
    phone = HOME.split('return (\n    <div style={page}', 1)[1]
    assert 'data-testid="search-button"' in phone


def test_pill_geometry():
    assert 'clamp(184px, 31vh, 372px)' in PILL
    assert 'width:480px;height:52px' in PILL and 'border-radius:999px' in PILL
    # v227 (Taylor's R3): no key badges
    assert 'aria-label="Search"' in PILL and 'aria-hidden="true">/<' not in PILL
    assert 'lsp-key' not in PAL.split('export const paletteCss', 1)[0]


def test_palette_geometry_and_copy():
    assert 'calc(100vh - 24px - 420px)' in PAL and 'min(720px, calc(100vw - 96px))' in PAL
    for s in ("'In your library'", "'People'", "'No results'", "Couldn't search", 'Try again', 'placeholder="Search"'):
        assert s in PAL
    assert 'In Your Library' not in PAL and "'Find more'" not in PAL
    # PM ruling: prod 1b4ea74's collapsed "Find More" button, label as is.
    assert '>Find More<ChevronDown />' in PAL and '<ChevronLeft />Find More<' in PAL
    assert 'prefers-reduced-motion' in PAL


def test_wide_host_uses_palette():
    assert '<GlobalSearchHost palette /><HomeSearchPalette />' in HOST
