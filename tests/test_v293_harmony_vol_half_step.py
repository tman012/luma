"""v293: Harmony volume half-step — FE Pointer Events hold-repeat; SW bump."""
import os
import re


def test_sw_v293_or_newer():
    root = os.path.join(os.path.dirname(__file__), '..')
    sw = open(os.path.join(root, 'static', 'sw.js')).read()
    stamp = open(os.path.join(root, 'static', 'sw-stamp.json')).read()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 293
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp


def test_use_hold_repeat_is_pointer_events_only():
    root = os.path.join(os.path.dirname(__file__), '..')
    src = open(
        os.path.join(root, 'frontend', 'src', 'components', 'rooms', 'FullScreenRemote.tsx')
    ).read()
    start = src.index('function useHoldRepeat')
    end = src.index('// Shared inline styles')
    hook = src[start:end]
    assert 'onPointerDown' in hook
    assert 'activePointerRef' in hook
    assert 'onTouchStart' not in hook
    assert 'onMouseDown' not in hook
