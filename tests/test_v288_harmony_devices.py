"""v288: Rooms sheet exposes Harmony Volume + Nav device; PUT persists; SW v288."""
import os
import re

from tests.test_v224_rooms_setup import API, _add, rs  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401


def test_sw_v288_or_newer():
    root = os.path.join(os.path.dirname(__file__), '..')
    sw = open(os.path.join(root, 'static', 'sw.js')).read()
    stamp = open(os.path.join(root, 'static', 'sw-stamp.json')).read()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 288
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp


def test_put_harmony_volume_and_nav_persist(rs):
    """Volume → room.harmonyVolumeDevice; Nav → primary device.harmonyNavigationDevice."""
    c, fake, _ = rs
    rid = _add(c, 'Family Room').get_json()['rooms'][0]['id']
    d = _req(c, 'put', f'{API}/{rid}', {
        'wake': 'harmony',
        'harmony_hub': '192.168.1.19',
        'harmony_activity': 'SHIELD TV',
        'harmony_volume_device': 'AV Receiver',
        'harmony_nav_device': 'NVIDIA Shield',
    }).get_json()
    room = next(r for r in d['rooms'] if r['id'] == rid)
    assert room['harmony_hub'] == '192.168.1.19'
    assert room['harmony_volume_device'] == 'AV Receiver'
    assert room['player']['wake'] == 'harmony'
    assert room['player']['harmony_activity'] == 'SHIELD TV'
    assert room['player']['harmony_nav_device'] == 'NVIDIA Shield'
    stored = fake._settings['devices']['rooms'][0]
    assert stored['harmonyHubIp'] == '192.168.1.19'
    assert stored['harmonyVolumeDevice'] == 'AV Receiver'
    assert stored['useHarmonyHub'] is True
    assert stored['devices'][0]['harmonyActivity'] == 'SHIELD TV'
    assert stored['devices'][0]['harmonyNavigationDevice'] == 'NVIDIA Shield'


def test_get_reads_nav_from_device_then_room(rs):
    """view_room: device nav wins; room-level nav is fallback (matches remote path)."""
    c, fake, _ = rs
    rid = _add(c, 'Living Room').get_json()['rooms'][0]['id']
    stored = fake._settings['devices']['rooms'][0]
    stored['harmonyHubIp'] = '192.168.1.50'
    stored['harmonyVolumeDevice'] = 'AVR'
    stored['useHarmonyHub'] = True
    stored['harmonyNavigationDevice'] = 'RoomNav'
    stored['devices'][0]['powerMethod'] = 'harmony'
    # device empty → room fallback
    stored['devices'][0].pop('harmonyNavigationDevice', None)
    room = _req(c, 'get', API).get_json()['rooms'][0]
    assert room['harmony_volume_device'] == 'AVR'
    assert room['player']['harmony_nav_device'] == 'RoomNav'
    # device set → wins
    stored['devices'][0]['harmonyNavigationDevice'] = 'DeviceNav'
    room = _req(c, 'get', API).get_json()['rooms'][0]
    assert room['player']['harmony_nav_device'] == 'DeviceNav'


def test_put_rejects_unknown_key_still(rs):
    c, fake, _ = rs
    rid = _add(c, 'Office').get_json()['rooms'][0]['id']
    r = _req(c, 'put', f'{API}/{rid}', {'harmony_volume_device': 'AVR', 'extra': 1})
    assert r.status_code == 400
