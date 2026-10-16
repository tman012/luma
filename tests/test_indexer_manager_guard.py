"""#4's indexer manager goes through #2's single tracker guard (GUARD_LIMITS)."""
from unittest.mock import Mock, patch

import pytest

from app import indexer_api
from app.indexer_manager import IndexerManager, IndexerRateLimitError, IndexerBackoffError

pytestmark = pytest.mark.usefixtures('temp_data_dir')

XML = b'<?xml version="1.0"?><rss><channel><item><title>R</title><link>http://dl.test/1</link><guid>g</guid></item></channel></rss>'


def _manager(entries):
    s = Mock()
    s.get.side_effect = lambda k, d=None: entries if k == 'indexer.indexers' else (
        {'indexer.base_url': 'http://prowlarr.lan:9696', 'indexer.api_key': 'pk'}.get(k, d))
    return IndexerManager(s)


DIRECT = {'id': 'direct-1', 'name': 'Direct', 'url': 'http://tracker.example.com/api',
          'api_key': 'k', 'type': 'torznab', 'enabled': True}
PROXIED = {'id': 'prox-7', 'name': 'Proxied', 'url': '', 'api_key': '', 'type': 'torznab',
           'enabled': True, 'prowlarr_proxy': True, 'prowlarr_id': '7'}


def _resp(status=200, headers=None):
    r = Mock(status_code=status, headers=headers or {})
    r.raw = Mock(); r.raw.stream.return_value = [XML]
    r.raise_for_status = Mock()
    return r


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setitem(indexer_api.GUARD_LIMITS, 'min_interval_s', 0.0)
    indexer_api._guard_reset_memory()


_seq = [0]


def _run(m, n, interactive, ix='direct-1', resp=None):
    with patch('app.indexer_manager.validate_indexer_url', return_value=(True, '', '203.0.113.5')), \
         patch('app.indexer_manager.pinned_get', return_value=resp or _resp()) as g:
        for _ in range(n):
            _seq[0] += 1     # distinct queries: never a cache hit
            m._search_one_indexer(ix, f'q{_seq[0]}', [2000], 10, interactive=interactive)
    return g


def test_direct_interactive_cap_is_guards_40():
    m = _manager([DIRECT])
    _run(m, indexer_api.GUARD_LIMITS['interactive_per_hour'], True)
    with pytest.raises(IndexerRateLimitError, match='interactive hourly cap 40'):
        _run(m, 1, True)


def test_direct_background_bucket_is_guards_5():
    m = _manager([DIRECT])
    _run(m, indexer_api.GUARD_LIMITS['background_burst'], False)
    with pytest.raises(IndexerRateLimitError, match='token bucket'):
        _run(m, 1, False)


def test_proxied_entry_shares_prowlarr_counter():
    assert indexer_api.guard_key('prox-7', '7') == 7
    m = _manager([PROXIED])
    _run(m, 2, True, ix='prox-7')
    with indexer_api._budget_state_guard:
        assert len(indexer_api._guard_log[7]) == 2


def test_global_cap_covers_manager(monkeypatch):
    monkeypatch.setitem(indexer_api.GUARD_LIMITS, 'global_per_hour', 3)
    with indexer_api._budget_state_guard:
        for _ in range(3):
            assert indexer_api._guard_decide(99, True, __import__('time').time()) is None
    with pytest.raises(IndexerRateLimitError, match='global hourly cap'):
        _run(_manager([DIRECT]), 1, True)


def test_counters_survive_restart():
    m = _manager([DIRECT])
    _run(m, 5, False)
    indexer_api._guard_reset_memory()          # simulated restart: reload from data dir
    with pytest.raises(IndexerRateLimitError):
        _run(_manager([DIRECT]), 1, False)


def test_429_cooldown_persists_and_skips_http():
    m = _manager([DIRECT])
    with pytest.raises(IndexerBackoffError):
        _run(m, 1, True, resp=_resp(429, {'Retry-After': '600'}))
    indexer_api._guard_reset_memory()
    with pytest.raises(IndexerBackoffError, match='cooldown'):
        _run(_manager([DIRECT]), 1, True)
    key = indexer_api.guard_key('direct-1')
    assert indexer_api.guard_blocked_until(key)


def test_400_streak_doubles_and_success_resets(monkeypatch):
    key = indexer_api.guard_key('direct-1')
    m = _manager([DIRECT])
    t0 = 1_000_000.0
    u1 = indexer_api.guard_start_cooldown(key, 400, None, False, now=t0)
    u2 = indexer_api.guard_start_cooldown(key, 400, None, False, now=t0)
    base = indexer_api.GUARD_LIMITS['cooldown_400_s']
    assert u1 - t0 == base and u2 - t0 == 2 * base
    indexer_api._guard_cooldown.pop(key)
    _run(m, 1, True)
    assert key not in indexer_api._guard_streak


def test_strict_host_400_uses_strict_tier():
    assert indexer_api.is_strict_host('https://privatehd.to/api')
    key = indexer_api.guard_key('x')
    u = indexer_api.guard_start_cooldown(key, 400, None, True, now=0.0)
    assert u == indexer_api.GUARD_LIMITS['cooldown_400_strict_s']


def test_manager_has_no_second_limiter():
    import inspect
    from app import indexer_manager
    src = inspect.getsource(indexer_manager.IndexerManager._search_one_indexer)
    assert 'guard_acquire' in src
    assert 'bucket.take' not in src and 'requests_last_hour' not in src


def test_download_links_use_url_base():
    from flask import Flask
    from app.indexer_backend import indexer_download_path
    assert indexer_download_path('t1') == '/api/indexer/download/t1'
    a = Flask('x'); a.config['URL_BASE'] = '/luma'
    with a.app_context():
        assert indexer_download_path('t1') == '/luma/api/indexer/download/t1'
    from app import main
    assert main._INDEXER_DOWNLOAD_PATH.match('/luma/api/indexer/download/t1')
