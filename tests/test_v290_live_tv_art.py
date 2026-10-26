"""v290: Live TV empty-art fallback on Rooms + FullScreenRemote; SW v290."""
import os
import re


def test_sw_v290_or_newer():
    root = os.path.join(os.path.dirname(__file__), '..')
    sw = open(os.path.join(root, 'static', 'sw.js')).read()
    stamp = open(os.path.join(root, 'static', 'sw-stamp.json')).read()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 290
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp


def test_live_tv_art_files_present():
    root = os.path.join(os.path.dirname(__file__), '..')
    rooms = open(os.path.join(root, 'frontend', 'src', 'components', 'rooms', 'RoomsSheet.tsx')).read()
    remote = open(os.path.join(root, 'frontend', 'src', 'components', 'rooms', 'FullScreenRemote.tsx')).read()
    helper = open(os.path.join(root, 'frontend', 'src', 'components', 'rooms', 'liveTvArt.tsx')).read()
    assert 'Live TV' in rooms and 'bg-sky-950' in rooms
    assert 'TiViMate' in rooms and 'bg-purple-600' in rooms
    assert '#0b1220' in remote and '#0c4a6e' in remote
    assert 'isLiveTvNoArt' in helper and "return 'Live TV'" in helper
