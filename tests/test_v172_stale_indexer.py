"""v172 (tracker port step 4): an install upgraded with an old Prowlarr/Torznab indexer saved.

Nothing crashes and nothing is lost: the indexer.* settings stay as saved (api_key still masked
on read and kept on a masked save), are never used to search or grab, and are logged once; the
old indexer_guard.json is left byte-for-byte as it was (only Import from Prowlarr reads it, read
only). Searches find nothing until a tracker is on; the old Test route answers 410. Fakes only:
any socket connect fails the test.
"""
import hashlib
import json
import logging
import socket

import pytest

import app.main
from app import first_run, indexer_api, tracker_search, tracker_store
from app.settings_manager import SECRET_MASK, SettingsManager
from app.tracker_store import TrackerStore
from tests.test_downloads_add_ssrf import _owner_client, _require_auth_everywhere, client  # noqa: F401

STALE = {'provider': 'prowlarr', 'base_url': 'http://192.168.1.30:9696', 'api_key': 'prowlarr_key_0123456789abcdef',
         'torznab_url': 'http://192.168.1.30:9117/api/v2.0/indexers/all/results/torznab', 'movie_categories': '2000,2040',
         'max_age_days': 30, 'min_seeders': 1, 'limit': 100}
GUARD = {'state': {'cooldown_until': 0, 'per_indexer': {'7': {'until': 9999999999, 'reason': 'rate'}}},
         'log': [1, 2, 3]}


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError('nothing may be contacted')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket, 'create_connection', refuse)


@pytest.fixture
def stale(test_app, tmp_path, monkeypatch, no_network):
    """The running app with the old indexer saved, an old guard file in the data folder and no trackers."""
    monkeypatch.setattr(first_run, 'data_dir', lambda: tmp_path)
    guard = tmp_path / 'indexer_guard.json'
    guard.write_text(json.dumps(GUARD))
    store = TrackerStore(tmp_path / 'trackers.json', tmp_path / 'tracker_secrets.key')
    tracker_store.reset_store_for_tests(store)
    tracker_search.reset_search_for_tests(None)
    app.main.settings.update({'indexer': dict(STALE)})
    app.main._all_results_cache.clear()
    monkeypatch.setattr(indexer_api, '_legacy_logged', [False])
    yield {'guard': guard, 'guard_sha': hashlib.sha256(guard.read_bytes()).hexdigest(), 'store': store}
    tracker_store.reset_store_for_tests(None)
    tracker_search.reset_search_for_tests(None)
    app.main.settings.update({'indexer': {'base_url': '', 'api_key': '', 'torznab_url': ''}})


def _owner(client):  # noqa: F811
    _require_auth_everywhere()
    return _owner_client(client)


def test_the_saved_indexer_is_kept_masked_and_survives_a_save(stale, client):  # noqa: F811
    c = _owner(client)
    r = c.get('/api/settings', environ_base={'REMOTE_ADDR': '192.168.1.20'})
    assert r.status_code == 200
    ix = r.get_json()['indexer']
    assert ix['api_key'] == SECRET_MASK and ix['base_url'] == STALE['base_url']
    # the UI saves another section with the masked indexer echoed back: nothing is lost
    r = c.put('/api/settings', json={'indexer': ix, 'ui': {'theme': 'dark'}},
              headers={'Origin': 'http://localhost'}, environ_base={'REMOTE_ADDR': '192.168.1.20'})
    assert r.status_code in (200, 204), r.get_data(as_text=True)
    for k, v in STALE.items():
        assert app.main.settings.get(f'indexer.{k}') == v, k


def test_searching_finds_nothing_contacts_nothing_and_logs_once(stale, client, caplog):  # noqa: F811
    c = _owner(client)
    caplog.set_level(logging.WARNING, logger='app.indexer_api')
    for _ in range(3):
        assert indexer_api.get_indexer_client(app.main.settings) is None
        r = c.get('/api/sources/all-results?title=Heat&media_type=movie', environ_base={'REMOTE_ADDR': '192.168.1.20'})
        assert r.status_code in (200, 503), r.get_data(as_text=True)
        if r.status_code == 503:
            assert r.get_json()['error'] == 'No trackers are on. Turn one on in Settings > Trackers.'
        else:
            assert not r.get_json().get('releases')
    assert sum('no longer used' in m for m in caplog.messages) == 1


def test_the_old_test_route_is_gone_without_contacting_anything(stale, client):  # noqa: F811
    c = _owner(client)
    r = c.post('/api/settings/test/indexer', json={'provider': 'prowlarr', 'base_url': STALE['base_url'],
                                                   'api_key': SECRET_MASK},
               headers={'Origin': 'http://localhost'}, environ_base={'REMOTE_ADDR': '192.168.1.20'})
    assert r.status_code == 410
    assert r.get_json()['gone'] is True and r.get_json()['success'] is False


def test_integrations_status_says_not_searching(stale, client):  # noqa: F811
    c = _owner(client)
    r = c.get('/api/integrations/status', environ_base={'REMOTE_ADDR': '192.168.1.20'})
    assert r.status_code == 200
    assert r.get_json()['indexer'] is False


def test_the_old_guard_file_is_left_untouched(stale, client):  # noqa: F811
    c = _owner(client)
    c.get('/api/sources/all-results?title=Heat&media_type=movie', environ_base={'REMOTE_ADDR': '192.168.1.20'})
    c.get('/api/integrations/status', environ_base={'REMOTE_ADDR': '192.168.1.20'})
    assert hashlib.sha256(stale['guard'].read_bytes()).hexdigest() == stale['guard_sha']


def test_a_settings_file_with_the_old_indexer_loads_and_saves_without_loss(tmp_path):
    p = tmp_path / 'settings.json'
    p.write_text(json.dumps({'indexer': dict(STALE), 'plex': {'url': 'http://plex.test:32400'}}))
    sm = SettingsManager(str(p))
    assert sm.get('indexer.api_key') == STALE['api_key']
    sm.update({'plex': {'url': 'http://plex2.test:32400'}})
    saved = json.loads(p.read_text())
    moved = ('movie_categories',)   # v173: now tracker_search.*
    gone = ('max_age_days', 'min_seeders', 'limit')   # v175 (Ops): the unwired rest is stripped at load
    for k, v in STALE.items():
        if k in gone:
            assert k not in saved['indexer'] and k not in saved['tracker_search'], k
            continue
        assert (saved['tracker_search'] if k in moved else saved['indexer'])[k] == v, k
    assert indexer_api.legacy_configured(sm) is True


def test_the_ui_has_no_indexer_connection_left():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / 'frontend' / 'src'
    hits = []
    for f in list(src.rglob('*.ts')) + list(src.rglob('*.tsx')):
        text = f.read_text(encoding='utf-8')
        for needle in ('/api/settings/test/indexer', 'IndexerCard', 'No indexer configured'):
            if needle in text:
                hits.append(f'{f.name}: {needle}')
    assert hits == []
