"""v285: Rooms remote Harmony Hub by room IP stays open under ADB allowlist; SW v285."""
import os
import re

from app import adb_utils
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401


def test_sw_v285_or_newer():
    root = os.path.join(os.path.dirname(__file__), '..')
    sw = open(os.path.join(root, 'static', 'sw.js')).read()
    stamp = open(os.path.join(root, 'static', 'sw-stamp.json')).read()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 285
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp


def test_harmony_volume_under_allowlist(minimal_app, owner, monkeypatch):
    """Direct Hub: vol_up reaches harmony_service when room has Hub IP + volume device."""
    import app.main as M
    from app import harmony_service
    from tests.test_home_rails import REMOTE

    calls = []
    monkeypatch.setattr(harmony_service, 'send_command', lambda ip, d, c: calls.append((ip, d, c)) or True)
    monkeypatch.setattr(adb_utils, '_ADB_ALLOWED', frozenset({'connect', 'disconnect', 'devices', 'pair'}))
    monkeypatch.delenv('LUMA_DISABLE_ADB', raising=False)
    assert adb_utils.adb_allowlist_active() is True

    room = {
        'id': 'lr', 'name': 'Living Room', 'useHarmonyHub': True, 'harmonyHubIp': '192.168.1.50',
        'harmonyVolumeDevice': 'Receiver',
        'devices': [{'id': 'd1', 'host': '192.168.1.40', 'connectPort': 5555, 'services': []}],
    }
    monkeypatch.setattr(M.settings, 'get_room_by_id', lambda rid: room)
    r = owner.post('/api/room/lr/remote', json={'command': 'vol_up'},
                   environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json().get('ok') is True
    assert calls == [('192.168.1.50', 'Receiver', 'VolumeUp')]


def test_harmony_transport_under_allowlist(minimal_app, owner, monkeypatch):
    """play_pause / mute / back go to Hub nav or volume device under allowlist."""
    import app.main as M
    from app import harmony_service
    from tests.test_home_rails import REMOTE

    calls = []
    monkeypatch.setattr(harmony_service, 'send_command', lambda ip, d, c: calls.append((d, c)) or True)
    monkeypatch.setattr(adb_utils, '_ADB_ALLOWED', frozenset({'connect', 'disconnect', 'devices', 'pair'}))
    monkeypatch.delenv('LUMA_DISABLE_ADB', raising=False)

    room = {
        'id': 'lr', 'name': 'Living Room', 'useHarmonyHub': True, 'harmonyHubIp': '192.168.1.50',
        'harmonyVolumeDevice': 'Receiver',
        'devices': [{
            'id': 'd1', 'host': '192.168.1.40', 'connectPort': 5555,
            'harmonyNavigationDevice': 'Shield', 'services': [],
        }],
    }
    monkeypatch.setattr(M.settings, 'get_room_by_id', lambda rid: room)
    for cmd, device, harm in (
        ('play_pause', 'Shield', 'Pause'),
        ('mute', 'Receiver', 'Mute'),
        ('back', 'Shield', 'Back'),
        ('vol_down', 'Receiver', 'VolumeDown'),
    ):
        calls.clear()
        r = owner.post('/api/room/lr/remote', json={'command': cmd}, environ_base=REMOTE, headers=HDR)
        assert r.status_code == 200 and r.get_json().get('ok') is True, (cmd, r.get_json())
        assert (device, harm) in calls, (cmd, calls)


def test_harmony_opens_by_hub_ip_without_flag(minimal_app, owner, monkeypatch):
    """Hub IP alone is enough — useHarmonyHub flag not required for remote keys."""
    import app.main as M
    from app import harmony_service
    from tests.test_home_rails import REMOTE

    calls = []
    monkeypatch.setattr(harmony_service, 'send_command', lambda ip, d, c: calls.append((ip, d, c)) or True)
    monkeypatch.setattr(adb_utils, '_ADB_ALLOWED', frozenset({'connect', 'disconnect', 'devices', 'pair'}))
    monkeypatch.delenv('LUMA_DISABLE_ADB', raising=False)

    room = {
        'id': 'lr', 'name': 'Living Room', 'useHarmonyHub': False, 'harmonyHubIp': '192.168.1.50',
        'harmonyVolumeDevice': 'Receiver',
        'devices': [{'id': 'd1', 'host': '192.168.1.40', 'connectPort': 5555, 'services': []}],
    }
    monkeypatch.setattr(M.settings, 'get_room_by_id', lambda rid: room)
    r = owner.post('/api/room/lr/remote', json={'command': 'mute'},
                   environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json().get('ok') is True
    assert calls == [('192.168.1.50', 'Receiver', 'Mute')]
