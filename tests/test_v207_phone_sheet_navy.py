"""v207 (PM): on test (LUMA_MINIMAL) the phone movie sheet is plain navy as prod shows: neither the poster
tint nor the /api/tmdb/colors sample reaches it; prod's path is unchanged."""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = 'frontend/src/components/media/MovieDetailModal/CompactLayout.tsx'


def test_minimal_sheet_has_no_palette():
    s = (ROOT / F).read_text()
    assert 'ultraBlurColors: paletteColors,' in s
    assert 'const ultraBlurColors = isMinimal() ? null : paletteColors;' in s
    i = s.index('const ultraBlurColors = isMinimal()')
    assert i < s.index('sheetBase(ultraBlurColors)') and i < s.index('useBackdropEffects(detail?.backdropUrl, ultraBlurColors)')
    assert "?? '#15102a'" in s                       # prod's navy fallback


def test_prod_palette_path_unchanged():
    for f in ('frontend/src/hooks/useModalColors.ts', 'frontend/src/services/modalColors.ts',
              'frontend/src/utils/posterColors.ts', 'frontend/src/components/media/UltraBlurOverlay.tsx'):
        prod = subprocess.run(['git', 'show', f'1b4ea74:{f}'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        assert prod == (ROOT / f).read_text(), f
