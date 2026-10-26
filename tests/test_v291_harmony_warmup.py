"""v291: Harmony first-press warmup — ensure path, idle timeout, WEBSOCKETS, connect_ms."""
import logging
import os
import re

from app import adb_utils
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401


def test_sw_v291_or_newer():
    root = os.path.join(os.path.dirname(__file__), '..')
    sw = open(os.path.join(root, 'static', 'sw.js')).read()
    stamp = open(os.path.join(root, 'static', 'sw-stamp.json')).read()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 291
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp


def test_idle_timeout_raised():
    from app import harmony_service
    assert harmony_service._IDLE_TIMEOUT >= 7200


def test_pool_uses_websockets_protocol():
    src = open(os.path.join(os.path.dirname(__file__), '..', 'app', 'harmony_service.py')).read()
    assert 'protocol="WEBSOCKETS"' in src or "protocol='WEBSOCKETS'" in src
    assert 'connect_ms' in src


def test_ensure_connected_warms_via_get_api(monkeypatch):
    from app import harmony_service

    calls = []

    def fake_run_on_hub(hub_ip, action):
        calls.append(hub_ip)
        return True

    monkeypatch.setattr(harmony_service, '_run_on_hub', fake_run_on_hub)
    assert harmony_service.ensure_connected('192.168.1.19') is True
    assert calls == ['192.168.1.19']


def test_ensure_endpoint_warms_when_hub_configured(minimal_app, owner, monkeypatch):
    import app.main as M
    from app import harmony_service
    from tests.test_home_rails import REMOTE

    calls = []
    monkeypatch.setattr(harmony_service, 'ensure_connected', lambda ip: calls.append(ip) or True)
    monkeypatch.setattr(adb_utils, '_ADB_ALLOWED', frozenset({'connect', 'disconnect', 'devices', 'pair'}))
    monkeypatch.delenv('LUMA_DISABLE_ADB', raising=False)

    room = {
        'id': 'fr', 'name': 'Family Room', 'useHarmonyHub': True, 'harmonyHubIp': '192.168.1.19',
        'harmonyVolumeDevice': 'Denon',
        'devices': [{'id': 'd1', 'host': '192.168.1.40', 'connectPort': 5555, 'services': []}],
    }
    monkeypatch.setattr(M.settings, 'get_room_by_id', lambda rid: room)
    r = owner.post('/api/room/fr/harmony/ensure', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    body = r.get_json()
    assert body.get('ok') is True and body.get('warmed') is True
    assert calls == ['192.168.1.19']

    r2 = owner.get('/api/room/fr/harmony/ensure', environ_base=REMOTE, headers=HDR)
    assert r2.status_code == 200 and r2.get_json().get('warmed') is True


def test_ensure_endpoint_noop_without_hub(minimal_app, owner, monkeypatch):
    import app.main as M
    from app import harmony_service
    from tests.test_home_rails import REMOTE

    calls = []
    monkeypatch.setattr(harmony_service, 'ensure_connected', lambda ip: calls.append(ip) or True)
    room = {
        'id': 'br', 'name': 'Bedroom', 'useHarmonyHub': False, 'harmonyHubIp': '',
        'devices': [{'id': 'd1', 'host': '192.168.1.41', 'connectPort': 5555, 'services': []}],
    }
    monkeypatch.setattr(M.settings, 'get_room_by_id', lambda rid: room)
    r = owner.post('/api/room/br/harmony/ensure', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    body = r.get_json()
    assert body.get('ok') is True and body.get('warmed') is False
    assert calls == []


def test_minimal_allows_harmony_ensure():
    from app import minimal
    assert minimal.path_allowed('/api/room/fr/harmony/ensure', 'POST')
    assert minimal.path_allowed('/api/room/fr/harmony/ensure', 'GET')
    assert not minimal.path_allowed('/api/room/fr/harmony/ensure', 'DELETE')


def test_connect_ms_logged_on_cold_connect(monkeypatch, caplog):
    """INFO connect_ms around api.connect() in _get_api."""
    import asyncio
    from app import harmony_service

    class FakeApi:
        def __init__(self, *a, **k):
            self.fw_version = None
            self.protocol = k.get('protocol')
        async def connect(self):
            await asyncio.sleep(0)
            self.fw_version = '4.15'
            return True
        async def close(self):
            pass

    monkeypatch.setattr(harmony_service, 'HarmonyAPI', FakeApi)
    # Use a private loop for this test so we don't disturb the module pool thread
    loop = asyncio.new_event_loop()

    async def run():
        # clear pool under lock object used by _get_api
        async with harmony_service._pool_lock:
            harmony_service._pool.clear()
        with caplog.at_level(logging.INFO, logger='app.harmony_service'):
            api = await harmony_service._get_api('10.0.0.9')
        return api

    # _pool_lock may be bound to another loop — recreate for this loop
    harmony_service._pool_lock = asyncio.Lock()
    api = loop.run_until_complete(run())
    assert api.fw_version == '4.15'
    assert api.protocol == 'WEBSOCKETS'
    joined = ' '.join(r.message for r in caplog.records)
    assert 'connect_ms=' in joined
    assert '10.0.0.9' in joined
    loop.close()
