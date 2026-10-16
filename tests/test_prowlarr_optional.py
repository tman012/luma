"""Luma must work fully with Prowlarr unconfigured, and with it configured but
unreachable: startup, search via direct indexers, the indexer settings APIs and
grabs, with no 5xx responses and no repeated error logging."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
TORRENT = b'd8:announce3:url4:infod4:name1:xee'
DIRECT = {'id': 'direct-1', 'name': 'Direct', 'url': 'https://tracker.example.com/api',
          'api_key': 'k', 'type': 'torznab', 'enabled': True, 'priority': 10}
PROXIED = {'id': 'prowlarr-7', 'name': 'AvistaZ (via Prowlarr)', 'url': '', 'api_key': '',
           'type': 'torznab', 'enabled': True, 'prowlarr_proxy': True, 'prowlarr_id': '7',
           'rate_limited': False, 'priority': 20}
XML = (b'<?xml version="1.0"?><rss><channel><item><title>Movie.2024.1080p</title>'
       b'<link>https://tracker.example.com/dl?id=1&amp;apikey=k</link><guid>g1</guid></item>'
       b'</channel></rss>')
UNREACHABLE = 'http://127.0.0.1:1'

SCENARIOS = {
    'unconfigured': {'indexer.base_url': '', 'indexer.api_key': ''},
    'unreachable': {'indexer.base_url': UNREACHABLE, 'indexer.api_key': 'pk'},
}


def _settings(extra):
    store = {'indexer.backend': 'luma', 'indexer.indexers': [dict(DIRECT), dict(PROXIED)],
             'qbittorrent.url': 'http://qbit.test:8080', 'qbittorrent.username': 'u',
             'qbittorrent.password': 'p'}
    store.update(extra)
    sm = Mock()
    sm.get.side_effect = lambda k, d=None: store.get(k, d)

    def update(payload):
        for section, values in payload.items():
            for k, v in values.items():
                store[f'{section}.{k}'] = v
        return True, []
    sm.update.side_effect = update
    sm.get_all = Mock(return_value={})
    sm.store = store
    return sm


def _xml_resp():
    r = Mock(status_code=200, headers={})
    r.raw = Mock()
    r.raw.stream.return_value = [XML]
    r.raise_for_status = Mock()
    return r


def _fake_network(url, pinned_ip, **kwargs):
    """Direct tracker answers; anything on the Prowlarr address is unreachable."""
    if url.startswith(UNREACHABLE):
        raise requests.exceptions.ConnectionError('Connection refused')
    if 'tracker.example.com/api' in url:
        return _xml_resp()
    if 'tracker.example.com/dl' in url:
        r = Mock(status_code=200, headers={})
        r.iter_content.side_effect = lambda chunk_size=8192: iter([TORRENT])
        return r
    raise AssertionError(f'unexpected fetch {url}')


class _FakeQbit:
    def __init__(self):
        self.added = []

    def post(self, url, data=None, files=None, timeout=None):
        if url.endswith('/torrents/add'):
            self.added.append({'data': data, 'files': files})
        return Mock(status_code=200, text='Ok.')

    def get(self, url, params=None, timeout=None):
        import hashlib
        items = [{'hash': hashlib.sha1(b'd4:name1:xe').hexdigest(), 'added_on': 1}] if self.added else []
        return Mock(status_code=200, json=Mock(return_value=items))


@pytest.fixture(params=sorted(SCENARIOS))
def env(request, temp_data_dir, caplog):
    from app import main
    from app.indexer_backend import LumaBackendAdapter
    from app.indexer_manager import IndexerManager
    sm = _settings(SCENARIOS[request.param])
    manager = IndexerManager(sm)
    adapter = LumaBackendAdapter(manager)
    caplog.set_level(logging.INFO)
    with patch.object(main, 'settings', sm), \
         patch.object(main, '_token_store', return_value=manager), \
         patch('app.indexer_api.get_indexer_client', return_value=adapter), \
         patch('app.indexer_manager.validate_indexer_url', return_value=(True, '', '203.0.113.9')), \
         patch('app.indexer_manager.pinned_get', side_effect=_fake_network) as net, \
         patch.object(main, '_get_current_user', return_value={'id': 1, 'name': 'o', 'is_admin': True}):
        main.app.config['TESTING'] = True
        yield request.param, main, adapter, net, caplog


def _no_errors(caplog):
    bad = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not bad, [r.getMessage() for r in bad]


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


def test_search_uses_direct_indexers(env):
    scenario, main, adapter, net, caplog = env
    for _ in range(3):
        results = adapter.search_all('Movie', 'movie', 2024)
        assert len(results) == 1 and results[0]['indexer_id'] == 'direct-1'
        assert results[0]['download_url'].startswith('/api/indexer/download/')
    _no_errors(caplog)
    # At most one warning for the unreachable Prowlarr (then it is backed off); none otherwise.
    assert len(_warnings(caplog)) <= (1 if scenario == 'unreachable' else 0), \
        [r.getMessage() for r in _warnings(caplog)]
    if scenario == 'unconfigured':
        assert not [c for c in net.call_args_list if '127.0.0.1' in c.args[0]]
        assert sum('Prowlarr is not configured' in r.getMessage() for r in caplog.records) == 1


def test_sources_panel_and_grab(env, monkeypatch):
    scenario, main, adapter, net, caplog = env
    monkeypatch.setattr(main, '_all_results_cache', {})
    c = main.app.test_client()
    r = c.get('/api/sources/all-results?title=Movie&media_type=movie&year=2024')
    assert r.status_code == 200
    rel = r.get_json()['releases']
    assert len(rel) == 1
    import sys as _sys
    qbit = _FakeQbit()
    with patch('requests.Session', return_value=qbit), patch('time.sleep'), \
         patch.dict(_sys.modules, {'app.auto_rename': Mock(auto_renamer=Mock())}), \
         patch.object(main, '_record_origin'):
        g = c.post('/api/downloads/add', json={'torrent_url': rel[0]['download_url']})
    assert g.status_code == 200, g.get_json()
    assert qbit.added[0]['files']['torrents'][1] == TORRENT
    with patch('time.sleep'):
        d = c.get(rel[0]['download_url'])
    assert d.status_code == 200 and d.data == TORRENT
    _no_errors(caplog)


def test_indexer_settings_apis(env):
    scenario, main, adapter, net, caplog = env
    c = main.app.test_client()
    r = c.get('/api/settings/indexers')
    assert r.status_code == 200 and {ix['id'] for ix in r.get_json()} == {'direct-1', 'prowlarr-7'}
    r = c.post('/api/settings/indexers', json={'name': 'New', 'url': 'https://new.example.com/api',
                                               'type': 'newznab', 'api_key': 'x'})
    assert r.status_code == 201
    new_id = r.get_json()['id']
    assert c.put(f'/api/settings/indexers/{new_id}', json={'name': 'Renamed'}).status_code == 200
    assert c.post('/api/settings/indexers/prowlarr-7/test').status_code == 200
    assert c.delete(f'/api/settings/indexers/{new_id}').status_code == 200
    imp = c.post('/api/settings/indexers/import-prowlarr', json={})
    assert imp.status_code < 500
    if scenario == 'unconfigured':
        assert imp.status_code == 400
    assert c.post('/api/settings/test/indexer', json={}).status_code < 500
    _no_errors(caplog)


def test_proxied_download_without_prowlarr_is_not_5xx(env):
    scenario, main, adapter, net, caplog = env
    tok = adapter.manager.issue_download_token(f'{UNREACHABLE}/7/download?link=x', 'prowlarr-7')
    r = main.app.test_client().get(f'/api/indexer/download/{tok}')
    if scenario == 'unconfigured':
        assert r.status_code == 409
    else:
        # An unreachable upstream is reported as a gateway error, without a traceback.
        assert r.status_code == 502 and r.get_json() == {'error': 'Could not reach indexer'}
    _no_errors(caplog)


def test_lan_allowance_only_with_prowlarr_url(env):
    scenario, main, adapter, net, caplog = env
    assert (main._prowlarr_origin() is None) is (scenario == 'unconfigured')
    with patch('app.indexer_manager.validate_indexer_url', return_value=(False, 'blocked', None)) as val:
        try:
            main._fetch_validated('http://127.0.0.1:1/7/download', allow_lan=False,
                                  trusted_origin=main._prowlarr_origin())
        except main.IndexerDownloadError:
            pass
    kwargs = val.call_args.kwargs
    if scenario == 'unconfigured':
        assert kwargs == {'allow_lan': False, 'allow_loopback_for_host': None}
    else:
        assert kwargs == {'allow_lan': True, 'allow_loopback_for_host': '127.0.0.1'}


@pytest.mark.parametrize('scenario', sorted(SCENARIOS))
def test_app_starts_without_reaching_prowlarr(scenario, tmp_path):
    """Importing the app neither fails nor logs Prowlarr errors."""
    data = tmp_path / 'startup-data'
    data.mkdir()
    cfg = {'indexer': {'backend': 'luma', 'indexers': [DIRECT, PROXIED],
                       'base_url': SCENARIOS[scenario]['indexer.base_url'],
                       'api_key': SCENARIOS[scenario]['indexer.api_key']},
           'media': {'database_path': str(data / 'media.db')}}
    (data / 'settings.json').write_text(json.dumps(cfg))
    env = dict(os.environ, DATABASE_PATH=str(data / 'media.db'), LUMA_DATA_DIR=str(data),
               LUMA_TEST_MODE='1', HERO_CACHE_DIR=str(tmp_path / 'hero'), PYTHONPATH=str(ROOT))
    code = ('import logging, app.main as m; '
            'print("BACKEND", m.settings.get("indexer.backend")); '
            'print("PROWLARR", repr(m.settings.get("indexer.base_url")))')
    proc = subprocess.run([sys.executable, '-c', code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert 'BACKEND luma' in proc.stdout
    assert f"PROWLARR {SCENARIOS[scenario]['indexer.base_url']!r}" in proc.stdout
    lines = (proc.stdout + proc.stderr).splitlines()
    assert not [l for l in lines if 'Traceback' in l]
    assert not [l for l in lines if 'prowlarr' in l.lower() and ('ERROR' in l or 'WARNING' in l)]
