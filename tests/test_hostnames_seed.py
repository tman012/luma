"""security.hostnames is seeded once on upgrade, from LAN-only sources (PM requirement)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import app.main
import app.user_auth as user_auth
from app import lan_hosts
from app.settings_manager import SettingsManager, _migrate_seed_hostnames

LAN = {'REMOTE_ADDR': '192.168.1.20'}
WAN = {'REMOTE_ADDR': '203.0.113.9'}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv('APP_URL', raising=False)
    monkeypatch.setattr(lan_hosts.socket, 'gethostname', lambda: 'docker-server')
    lan_hosts._last_write.clear()
    yield
    lan_hosts._last_write.clear()


def _history(tmp_path, **names_age_days):
    now = datetime.now(timezone.utc)
    names = {n.replace('_', '.'): (now - timedelta(days=d)).isoformat() for n, d in names_age_days.items()}
    (tmp_path / lan_hosts.FILENAME).write_text(json.dumps({'names': names}))


def test_absent_key_seeds_from_hostname_and_config(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_URL', 'http://luma.home.arpa:5001')
    data = {'security': {'auth_required': 'disabled_local'}}
    assert _migrate_seed_hostnames(data, tmp_path) is True
    assert data['security']['hostnames'] == \
        'luma.home.arpa, docker-server, docker-server.local, docker-server.lan'


def test_public_app_url_not_seeded(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_URL', 'https://luma.example.com')
    data = {}
    _migrate_seed_hostnames(data, tmp_path)
    assert 'example.com' not in data['security']['hostnames']


def test_present_key_untouched(tmp_path):
    _history(tmp_path, kiosk_lan=1)
    for value in ('', 'mine.lan'):
        data = {'security': {'hostnames': value}}
        assert _migrate_seed_hostnames(data, tmp_path) is False
        assert data['security']['hostnames'] == value


def test_lan_history_wins_and_filters(tmp_path):
    _history(tmp_path, kiosk_lan=2, tv=5, luma_example_com=1, old_lan=45)
    data = {'security': {}}
    _migrate_seed_hostnames(data, tmp_path)
    assert data['security']['hostnames'] == 'kiosk.lan, tv'   # newest first; public and >30 days dropped


def test_portal_host_never_seeded(tmp_path):
    _history(tmp_path, req_lan=1, kiosk_lan=1)
    data = {'security': {}, 'request_portal': {'hostname': 'req.lan'}}
    _migrate_seed_hostnames(data, tmp_path)
    assert 'req.lan' not in data['security']['hostnames']


def test_docker_container_id_not_seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(lan_hosts.socket, 'gethostname', lambda: '3f2a9c1b7d4e')
    data = {}
    _migrate_seed_hostnames(data, tmp_path)
    assert data['security']['hostnames'] == ''


def test_seeded_once_through_settings_manager(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'security': {'auth_required': 'disabled_local'}}))
    sm = SettingsManager(str(path))
    assert sm.get('security.hostnames') == 'docker-server, docker-server.local, docker-server.lan'
    assert 'docker-server' in json.loads(path.read_text())['security']['hostnames']
    sm.update({'security': {'hostnames': ''}})
    assert SettingsManager(str(path)).get('security.hostnames') == ''   # never re-seeded


@pytest.mark.parametrize('name,ok', [
    ('docker-server', True), ('luma.lan', True), ('Kiosk.Local.', True), ('x.home.arpa', True),
    ('luma.example.com', False), ('localhost', False), ('192.168.1.62', False), ('[::1]', False),
    ('bad_name', False), ('', False),
])
def test_is_seedable(name, ok):
    assert lan_hosts.is_seedable(name) is ok


def test_recording_only_from_lan_non_portal(test_app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.main, '_LAN_HOSTS_DIR', tmp_path)
    user_auth.create_user('owner', '1234', is_admin=True)
    app.main.settings.update({'request_portal': {'hostname': 'req.lan'}})
    try:
        c = test_app.test_client()
        c.get('/api/auth/me', headers={'Host': 'kiosk.lan:5001'}, environ_base=LAN)
        c.get('/api/auth/me', headers={'Host': 'remote-only.lan'}, environ_base=WAN)
        c.get('/request', headers={'Host': 'req.lan'}, environ_base=LAN)
        c.get('/api/auth/me', headers={'Host': '192.168.1.62:5001'}, environ_base=LAN)
        c.get('/api/auth/me', headers={'Host': 'relayed.lan', 'X-Forwarded-For': '192.168.1.30'},
              environ_base={'REMOTE_ADDR': '10.0.0.1'})   # untrusted relay counts as remote
    finally:
        app.main.settings.update({'request_portal': {'hostname': ''}})
    assert set(lan_hosts.load(tmp_path)) == {'kiosk.lan'}
    data = {}
    _migrate_seed_hostnames(data, tmp_path)
    assert data['security']['hostnames'] == 'kiosk.lan'


def test_security_api_lists_unlisted_lan_names(test_app, tmp_path, monkeypatch):
    monkeypatch.setattr(app.main, '_LAN_HOSTS_DIR', tmp_path)
    _history(tmp_path, kiosk_lan=1, tv_lan=3)
    user_auth.create_user('owner', '1234', is_admin=True)
    app.main.settings.update({'security': {'hostnames': 'tv.lan'}})
    got = test_app.test_client().get('/api/security', environ_base=LAN).get_json()
    assert got['seen_hostnames'] == ['kiosk.lan']


def test_record_is_throttled_and_capped(tmp_path, monkeypatch):
    lan_hosts.record('a.lan', tmp_path)
    first = lan_hosts.load(tmp_path)['a.lan']
    lan_hosts.record('a.lan', tmp_path)
    assert lan_hosts.load(tmp_path)['a.lan'] == first
    for i in range(lan_hosts.MAX_NAMES + 5):
        lan_hosts.record(f'h{i}.lan', tmp_path)
    assert len(lan_hosts.load(tmp_path)) == lan_hosts.MAX_NAMES
