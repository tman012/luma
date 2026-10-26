"""v284: Rooms sheet mirrors Activity chrome; SW media-kiosk-v284."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sw_v284_or_newer():
    import re
    sw = (ROOT / 'static' / 'sw.js').read_text()
    stamp = (ROOT / 'static' / 'sw-stamp.json').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 284
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp


def test_rooms_sheet_mirrors_activity_height():
    rooms = (ROOT / 'frontend/src/components/rooms/RoomsSheet.tsx').read_text()
    activity = (ROOT / 'frontend/src/components/activity/ActivitySheet.tsx').read_text()
    height = "height: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)'"
    assert height in rooms
    assert height in activity
    assert 'maxHeight' not in rooms
    assert 'ENTER_DURATION = 600' in rooms
    assert 'data-sheet="RoomsSheet"' in rooms
    assert '>Rooms</h2>' in rooms
