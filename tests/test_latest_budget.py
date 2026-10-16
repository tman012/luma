"""Latest feed under the tracker guard: auto-grab priority, never caching an
empty or blocked answer, in-process vs HTTP parity, RTCF backoff."""

import types

import pytest
from flask import jsonify

import app.indexer_api as ia
from tests.test_tracker_guard import Clock, _Session  # noqa: F401  (shared fakes)

HOUR = 3600


# ---------------------------------------------------------------- guard reserve

@pytest.fixture
def guard(monkeypatch, tmp_path):
    c = Clock()
    monkeypatch.setattr(ia, 'time', types.SimpleNamespace(time=c.time, sleep=c.sleep))
    monkeypatch.setenv('LUMA_STATE_DIR', str(tmp_path))
    ia._guard_reset_memory()
    for name in ('_query_cache', '_indexer_last_ts', '_status_cache', '_skip_logged'):
        getattr(ia, name).clear()
    client = ia.ProwlarrClient('http://prowlarr.test', 'k')
    client.session = _Session()
    client.session.backoffs = []
    yield c, client
    ia._guard_reset_memory()


_n = [0]


def _bg(client, ix=7):
    _n[0] += 1
    return client._get_search([('query', f'z{_n[0]}'), ('type', 'search')], [ix], interactive=False)


def test_limits_unchanged_and_reserve_inside_burst():
    L = ia.GUARD_LIMITS
    assert (L['background_burst'], L['background_refill_s'], L['per_day'], L['global_per_hour'],
            L['interactive_per_hour']) == (5, 180.0, 300, 120, 40)
    assert 0 < L['background_grab_reserve'] < L['background_burst']


def test_explore_cannot_drain_grab_reserve(guard):
    clock, client = guard
    free = ia.GUARD_LIMITS['background_burst'] - ia.GUARD_LIMITS['background_grab_reserve']
    for _ in range(free):
        _bg(client)
    with pytest.raises(ia.IndexerSkipped, match='reserved for auto-grab'):
        _bg(client)
    assert len(client.session.calls) == free


def test_grab_runs_right_after_explore_burst(guard):
    clock, client = guard
    for _ in range(10):  # Explore tries far more than its share
        try:
            _bg(client)
        except ia.IndexerSkipped:
            pass
    with ia.search_context(priority='grab'):
        for _ in range(ia.GUARD_LIMITS['background_grab_reserve']):
            _bg(client)
        with pytest.raises(ia.IndexerSkipped, match='bucket empty'):
            _bg(client)
    assert len(client.session.calls) == ia.GUARD_LIMITS['background_burst']  # same total


def test_grab_priority_reaches_worker_threads(guard):
    clock, client = guard
    seen = []
    from concurrent.futures import ThreadPoolExecutor
    with ia.search_context(priority='grab', strict=True):
        with ThreadPoolExecutor(2) as pool:
            f = ia._submit(pool, lambda: (ia._SEARCH_PRIORITY.get(), ia._STRICT_SKIPS.get()))
            seen.append(f.result())
    assert seen == [('grab', True)]
    assert ia._SEARCH_PRIORITY.get() == 'background' and ia._STRICT_SKIPS.get() is False


# ------------------------------------------------------- strict "blocked" mode

def _skipping_client():
    c = object.__new__(ia.ProwlarrClient)

    def _skip(*a, **k):
        raise ia.IndexerSkipped('over budget')
    c._search_single = _skip
    c._parse_items = lambda raw: raw
    return c


def test_browse_and_sweep_raise_only_in_strict_mode():
    c = _skipping_client()
    assert c.search_movies() == []
    assert c.sweep_movies(queries=['a', 'b']) == []
    with ia.search_context(strict=True):
        with pytest.raises(ia.IndexerSkipped):
            c.search_movies()
        with pytest.raises(ia.IndexerSkipped):
            c.sweep_movies(queries=['a', 'b'])


def test_partial_sweep_is_not_blocked():
    c = object.__new__(ia.ProwlarrClient)
    c._parse_items = lambda raw: [{'seeders': 1, **r} for r in raw]

    def _one(q, *a, **k):
        if q == 'a':
            raise ia.IndexerSkipped('over budget')
        return [{'indexer': 'X', 'guid': q}]
    c._search_single = _one
    with ia.search_context(strict=True):
        assert [m['guid'] for m in c.sweep_movies(queries=['a', 'b'])] == ['b']


# ------------------------------------------------- /api/indexer/latest behavior

class _FakeClient:
    def __init__(self, browse=None, sweep=None, raise_skip=False, raise_err=False):
        self.browse, self.sweep, self.calls = browse or [], sweep or [], []
        self.raise_skip, self.raise_err = raise_skip, raise_err

    def _go(self, kind, items):
        self.calls.append((kind, ia._SEARCH_PRIORITY.get(), ia._STRICT_SKIPS.get()))
        if self.raise_skip:
            raise ia.IndexerSkipped('over budget')
        if self.raise_err:
            raise RuntimeError('boom')
        return list(items)

    def search_movies(self, categories='2000', limit=300):
        return self._go('browse', self.browse)

    def sweep_movies(self, categories='2000', queries=None, max_workers=3):
        return self._go('sweep', self.sweep)


def _item(guid, title='Some Movie', quality='2160p'):
    return {'guid': guid, 'title': f'{title}.2026.{quality}.REMUX-GRP', 'indexer': 'BHD',
            'seeders': 250, 'size': 10 ** 10, 'pub_date': '', 'quality': quality}


@pytest.fixture
def latest_env(test_app, monkeypatch):
    import app.main as m
    m._LATEST_TORRENTS_CACHE.clear()
    # TMDb enrichment off: the view keeps items it can't enrich
    monkeypatch.setattr(m, '_LATEST_TMDB_CACHE', {}, raising=False)
    state = {}

    def install(client):
        state['client'] = client
        monkeypatch.setattr(m, 'get_indexer_client', lambda s: client)
        return client
    yield m, install
    m._LATEST_TORRENTS_CACHE.clear()


def _http(test_app, qs):
    from tests.local_access import local_client
    return local_client(test_app).get('/api/indexer/latest' + qs)


@pytest.mark.parametrize('params,qs,kind', [
    ({'limit': 300}, '?limit=300', 'browse'),
    ({'limit': 600, 'sweep': 'remux2160p'}, '?limit=600&sweep=remux2160p', 'sweep'),
])
def test_in_process_matches_http(test_app, latest_env, params, qs, kind):
    m, install = latest_env
    items = [_item('g1'), _item('g2', 'Other Film')]
    install(_FakeClient(browse=items, sweep=items))
    direct = m.latest_releases(params)
    m._LATEST_TORRENTS_CACHE.clear()
    r = _http(test_app, qs)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert [x.get('guid') for x in direct] == [x.get('guid') for x in r.get_json()['results']]
    assert direct and {x['guid'] for x in direct} <= {'g1', 'g2'}


def test_in_process_runs_with_grab_priority_http_does_not(test_app, latest_env):
    m, install = latest_env
    c = install(_FakeClient(browse=[_item('g1')]))
    m.latest_releases({'limit': 300})
    m._LATEST_TORRENTS_CACHE.clear()
    _http(test_app, '?limit=300')
    assert c.calls[0] == ('browse', 'grab', True)
    assert c.calls[1] == ('browse', 'background', True)


def test_empty_result_is_not_cached(test_app, latest_env):
    m, install = latest_env
    c = install(_FakeClient(browse=[]))
    assert m.latest_releases({'limit': 300}) == []
    assert 'results' not in m._LATEST_TORRENTS_CACHE.get('browse', {})
    c.browse = [_item('g9')]
    assert [x['guid'] for x in m.latest_releases({'limit': 300})] == ['g9']
    assert len(c.calls) == 2


@pytest.mark.parametrize('kw', [{'raise_skip': True}, {'raise_err': True}])
def test_blocked_or_error_is_not_cached(test_app, latest_env, kw):
    m, install = latest_env
    c = install(_FakeClient(**kw, sweep=[_item('g1')]))
    with pytest.raises(RuntimeError):
        m.latest_releases({'limit': 600, 'sweep': 'remux2160p'})
    assert 'results' not in m._LATEST_TORRENTS_CACHE.get('remux2160p', {})
    c.raise_skip = c.raise_err = False
    assert [x['guid'] for x in m.latest_releases({'limit': 600, 'sweep': 'remux2160p'})] == ['g1']


def test_blocked_http_is_503_with_retry_after(test_app, latest_env):
    m, install = latest_env
    install(_FakeClient(raise_skip=True))
    r = _http(test_app, '?limit=300')
    assert r.status_code == 503
    body = r.get_json()
    assert body['error'] == 'indexers over budget' and body['retry_after'] > 0


def test_nonempty_result_is_cached(test_app, latest_env):
    m, install = latest_env
    c = install(_FakeClient(browse=[_item('g1')]))
    m.latest_releases({'limit': 300})
    m.latest_releases({'limit': 300})
    assert len(c.calls) == 1


# ------------------------------------------------------- jobs on a blocked feed

class _Radarr:
    def __init__(self, *a, **k):
        pass

    def get_root_folders(self):
        return [{'path': '/movies'}]

    def get_quality_profiles(self):
        return [{'id': 1}]


class _Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_both_jobs_survive_a_blocked_feed(test_app, latest_env, monkeypatch):
    import app.radarr_api
    from app import latest_auto_grab, upgrade_4k_auto_grab
    m, install = latest_env
    install(_FakeClient(raise_skip=True))
    monkeypatch.setattr(app.radarr_api, 'RadarrAPI', _Radarr)
    cfg = _Cfg({'radarr.url': 'http://radarr.invalid', 'radarr.api_key': 'k',
                'automation.latest_auto_grab.quality_profile_id': 1,
                'automation.upgrade_4k_auto_grab.quality_profile_id': 1,
                'automation.upgrade_4k_auto_grab.plex_4k_section_id': 7})
    out = latest_auto_grab._do_cycle(cfg)
    assert out.get('grabbed', 0) == 0 and out.get('error')
    out = upgrade_4k_auto_grab._do_cycle(cfg)
    assert out.get('grabbed', 0) == 0 and 'over budget' in out.get('error', '')
    assert m._LATEST_TORRENTS_CACHE.get('browse', {}).get('results') is None


def test_4k_job_starts_after_latest():
    from app import latest_auto_grab, upgrade_4k_auto_grab
    assert upgrade_4k_auto_grab._INITIAL_DELAY > latest_auto_grab._INITIAL_DELAY + 60


# ------------------------------------------------------------- RTCF backoff

class _R:
    def __init__(self, status):
        self.status_code = status

    def json(self):
        return {'reviews': []}


def test_rtcf_backs_off_after_first_401(monkeypatch, caplog):
    import app.rt_scraper as rt
    clock = [1_800_000_000.0]
    monkeypatch.setattr(rt, 'time', types.SimpleNamespace(time=lambda: clock[0], sleep=lambda s: None))
    monkeypatch.setitem(rt._rtcf_state, 'until', 0.0)
    scraper = object.__new__(rt.RTScraper) if hasattr(rt, 'RTScraper') else None
    if scraper is None:
        cls = next(v for v in vars(rt).values() if isinstance(v, type) and hasattr(v, '_fetch_critic_reviews'))
        scraper = object.__new__(cls)
    calls = []

    class _S:
        headers = {}

        def get(self, url, timeout=None, headers=None):
            calls.append(url)
            return _R(401)
    scraper.session = _S()
    caplog.set_level('INFO', logger='app.rt_scraper')
    for _ in range(3):
        scraper._fetch_critic_reviews('id1', 20)
        scraper._fetch_audience_reviews('id1', 20)
    assert len(calls) == 1
    assert len([r for r in caplog.records if 'paused' in r.getMessage()]) == 1
    assert rt.RTCF_BACKOFF_S >= 3600
    clock[0] += rt.RTCF_BACKOFF_S + 1
    scraper._fetch_critic_reviews('id1', 20)
    assert len(calls) == 2
