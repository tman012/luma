"""v284: Rooms sheet mirrors Activity chrome; SW media-kiosk-v284."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sw_v284():
    sw = (ROOT / 'static' / 'sw.js').read_text()
    stamp = (ROOT / 'static' / 'sw-stamp.json').read_text()
    assert "CACHE_VERSION = 'media-kiosk-v284'" in sw
    assert '"cache_version": "media-kiosk-v284"' in stamp


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
