"""v301: phone TV calendar loading skeletons; v322 retires them (SPEC-fade-in); SW bumped."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_v322_phone_loading_has_no_skeletons():
    """v322 (TOKENS-C / SPEC-fade-in supersede the v301 skeletons): phone loading keeps the day nav and reserves one
    card's height, with no placeholder cards, shimmer or spinner."""
    src = (ROOT / 'frontend/src/components/library/TvCalendar.tsx').read_text()
    assert 'function PhonePlaceholder' not in src
    assert '<div data-testid="tv-cal-loading-phone" aria-busy="true" style={{ minHeight: GLASS_CARD_MIN_H }} />' in src
    assert '{phone ? <DayHeader day={day} today={today} onStep={step} />' in src
    phone = src[src.index('export function PhoneDay'):]
    assert 'skeleton-shimmer' not in phone and 'spinner' not in phone.lower()


def test_sw_v301():
    sw = (ROOT / 'static/sw.js').read_text()
    stamp = (ROOT / 'static/sw-stamp.json').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 301
    assert f"media-kiosk-v{m.group(1)}" in stamp
