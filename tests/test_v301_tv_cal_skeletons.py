"""v301: phone TV calendar loading = week strip (no dots) + 5 Home-style skeleton cards; SW bumped."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_phone_placeholder_variant_b_loading():
    src = (ROOT / 'frontend/src/components/library/TvCalendar.tsx').read_text()
    assert 'function PhonePlaceholder' in src
    i = src.index('function PhonePlaceholder')
    j = src.index('\nexport const AIR_SIZE', i)
    block = src[i:j]
    assert 'skeleton-shimmer' in block
    assert 'tv-cal-loading-phone' in block
    assert 'tv-cal-loading-strip' in block
    assert 'tv-cal-loading-row' in block
    assert 'rgba(255,255,255,0.14)' in block  # neutral edge
    assert 'spinner' not in block.lower()
    # five skeleton cards (SPEC-v187 / variant-B)
    assert "['70%', '50%']" in block or 'widths' in block


def test_phone_loading_hides_day_header_path():
    """First paint on phone must not flash DayHeader+desktop; loading owns the strip."""
    src = (ROOT / 'frontend/src/components/library/TvCalendar.tsx').read_text()
    assert 'loading ? null : <DayHeader' in src or '(loading ? null : <DayHeader' in src


def test_sw_v301():
    sw = (ROOT / 'static/sw.js').read_text()
    stamp = (ROOT / 'static/sw-stamp.json').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 301
    assert f"media-kiosk-v{m.group(1)}" in stamp
