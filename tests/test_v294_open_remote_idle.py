"""v294: Open Remote idle — FE never silent no-op; SW bump."""
import os
import re


def test_sw_v294_or_newer():
    root = os.path.join(os.path.dirname(__file__), '..')
    sw = open(os.path.join(root, 'static', 'sw.js')).read()
    stamp = open(os.path.join(root, 'static', 'sw-stamp.json')).read()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 294
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp


def test_rooms_sheet_open_remote_idle_paths():
    root = os.path.join(os.path.dirname(__file__), '..')
    src = open(
        os.path.join(root, 'frontend', 'src', 'components', 'rooms', 'RoomsSheet.tsx')
    ).read()
    assert 'Add a device in Settings.' in src
    assert 'hasHub' in src
    assert 'onNeedDevice' in src
    assert "autoDeviceId || roomDevices?.[0]?.id || ''" in src
    assert 'if (devId || hasHub)' in src
    assert 'if (devId) onOpenRemote(devId);' not in src
    assert 'rooms-device-notice' in src
    assert 'deviceId may be' in src


def test_rooms294_vitest_file_present():
    root = os.path.join(os.path.dirname(__file__), '..')
    path = os.path.join(root, 'frontend', 'tests', 'rooms294.test.tsx')
    assert os.path.isfile(path)
    body = open(path).read()
    assert 'v294' in body
    assert 'Add a device in Settings.' in body
