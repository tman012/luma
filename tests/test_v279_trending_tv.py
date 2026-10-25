"""v279 Settings > Automation > Trending TV (prod 1b4ea74 trending_tv_watchlist.py, native: shows via TV Add).
Trakt, TMDB, Plex and tv.db are replaced here."""
import pytest

from app.automation import gr_store, ttv, ttv_routes
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_v182_series_detail import _req

API = '/api/settings/automation/trending-tv'
NOW = 1_800_000_000.0


def show(tmdb, tvdb=None, title=None, year=2026):
    return {'watchers': 100, 'show': {'title': title or f'Show {tmdb}', 'year': year,
                                      'ids': {'tmdb': tmdb, 'tvdb': tvdb if tvdb is not None else tmdb + 1000}}}


class World:
    def __init__(self):
        self.trakt = []
        self.busy = False
        self.trakt_calls = []
        self.meta = {}          # tmdb -> {language, seasons}
        self.plex = {'tv': {'y|1': {}}, 'tv_by_tmdb': {}, 'tv_by_tvdb': {}}
        self.plex_error = False
        self.tv_db = set()
        self.tv_adds = []


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setitem(gr_store._cfg, 'db_path', str(tmp_path / 'automation.db'))
    monkeypatch.setattr(ttv, '_legacy', ttv.defaults)
    world = World()

    def fetch(cid, page, limit):
        world.trakt_calls.append((page, limit))
        if world.busy:
            raise ttv.Busy()
        return world.trakt[:limit]

    def plex():
        if world.plex_error:
            raise RuntimeError('plex down')
        return world.plex

    def tv_add(tmdb, path_id, monitor, profile_id, search):
        world.tv_adds.append((tmdb, path_id, monitor, profile_id, search))
        world.tv_db.add(tmdb)
        return {'ok': True, 'series_id': len(world.tv_adds)}

    monkeypatch.setattr(ttv, 'trakt_client_id', lambda: 'cid')
    monkeypatch.setattr(ttv, 'fetch_trakt', fetch)
    monkeypatch.setattr(ttv, 'show_details', lambda t: world.meta.get(t, {'language': 'en', 'seasons': 1}))
    monkeypatch.setattr(ttv, 'plex_index', plex)
    monkeypatch.setattr(ttv, 'in_tv_db', lambda tmdb, tvdb: tmdb in world.tv_db)
    monkeypatch.setattr(ttv, 'libraries', lambda: [{'id': 'p_tv', 'name': 'TV'}, {'id': 'p_kids', 'name': 'Kids TV'}])
    monkeypatch.setattr(ttv, 'tv_add', tv_add)
    return world


def on(**kw):
    ttv.save({'enabled': True, **kw})


# ------------------------------------------------------------------ settings

def test_defaults_match_prod_native_on(w):
    s = ttv.settings()
    assert s['enabled'] is True
    assert s['limit'] == 10 and s['search_on_add'] is True and s['languages'] == ['en']
    assert s['one_season_only'] is True and s['interval_hours'] == 2
    assert s['monitor'] == 'all' and s['require_tvdb'] is True and s['skip_plex'] is True
    assert s['when_busy'] == 'skip' and s['history_kept'] == 200


def test_validation(w):
    with pytest.raises(ttv.Invalid) as e:
        ttv.save({'limit': 0})
    assert 'limit' in e.value.errors
    with pytest.raises(ttv.Invalid) as e:
        ttv.save({'languages': ['xx']})
    assert 'languages' in e.value.errors
    with pytest.raises(ttv.Invalid) as e:
        ttv.save({'bogus': True})
    assert 'bogus' in e.value.errors


def test_legacy_prod_keys_import_keeps_enabled_true(tmp_path, monkeypatch):
    monkeypatch.setitem(gr_store._cfg, 'db_path', str(tmp_path / 'automation.db'))

    class Fake:
        def get(self, k, default=None):
            return {'automation.interval_hours': 3,
                    'automation.trending_tv_watchlist.limit': 7,
                    'automation.trending_tv_watchlist.search_on_add': False,
                    'automation.trending_tv_watchlist.allowed_languages': ['en', 'fr']}.get(k, default)

    monkeypatch.setattr('app.settings_manager.SettingsFile', Fake)
    s = ttv.settings()
    assert s['enabled'] is True and s['limit'] == 7 and s['search_on_add'] is False
    assert s['languages'] == ['en', 'fr'] and s['interval_hours'] == 3


# ------------------------------------------------------------------ cycle

def test_adds_trending_shows_natively(w):
    w.trakt = [show(1), show(2), show(3)]
    on(path_id='p_kids', search_on_add=False, profile_id=7)
    r = ttv.run('manual', now=NOW)
    assert r['added'] == 3 and r['checked'] == 3 and not r['error']
    assert w.tv_adds == [(1, 'p_kids', 'all', 7, False), (2, 'p_kids', 'all', 7, False), (3, 'p_kids', 'all', 7, False)]
    assert [h['tmdb_id'] for h in ttv.history()] == [3, 2, 1]


def test_limit_and_page(w):
    w.trakt = [show(i) for i in range(1, 20)]
    on(limit=5, page=2)
    r = ttv.run('manual', now=NOW)
    assert w.trakt_calls == [(2, 5)] and r['checked'] == 5 and len(w.tv_adds) == 5


def test_one_season_only_filter(w):
    w.trakt = [show(1), show(2), show(3)]
    w.meta = {1: {'language': 'en', 'seasons': 1},
              2: {'language': 'en', 'seasons': 3},
              3: {'language': 'en', 'seasons': -1}}
    on()
    r = ttv.run('manual', now=NOW)
    assert [a[0] for a in w.tv_adds] == [1]
    assert r['skipped']['seasons'] == 2
    w.tv_db.clear(); w.tv_adds.clear()
    on(one_season_only=False, no_readd=False)   # re-check the same list without the season gate
    r = ttv.run('manual', now=NOW)
    assert [a[0] for a in w.tv_adds] == [1, 2, 3]


def test_language_and_existing_and_plex(w):
    w.trakt = [show(1), show(2), show(3), show(4)]
    w.meta = {1: {'language': 'en', 'seasons': 1},
              2: {'language': 'ja', 'seasons': 1},
              3: {'language': 'en', 'seasons': 1},
              4: {'language': 'en', 'seasons': 1}}
    w.tv_db.add(3)
    w.plex['tv_by_tmdb'][4] = {}
    on()
    r = ttv.run('manual', now=NOW)
    assert [a[0] for a in w.tv_adds] == [1]
    assert r['skipped']['language'] == 1 and r['skipped']['existing'] == 1 and r['skipped']['plex'] == 1


def test_scheduled_respects_enabled_not_auto_grab(w):
    w.trakt = [show(1)]
    ttv.save({'enabled': False})
    r = ttv.run('scheduled', now=NOW)
    assert r['added'] == 0 and not w.tv_adds
    r = ttv.run('manual', now=NOW)          # Run now ignores the switch
    assert r['added'] == 1 and w.tv_adds


def test_no_readd(w):
    w.trakt = [show(1)]
    on()
    ttv.run('manual', now=NOW)
    w.tv_db.clear(); w.tv_adds.clear()
    r = ttv.run('manual', now=NOW + 1)
    assert r['skipped']['removed'] == 1 and not w.tv_adds
    on(no_readd=False)
    r = ttv.run('manual', now=NOW + 2)
    assert [a[0] for a in w.tv_adds] == [1]


def test_status_next_run_when_enabled(w):
    st = ttv.status()
    assert st['enabled'] is True and st['next_run'] is not None and st['running'] is False
    ttv.save({'enabled': False})
    assert ttv.status()['next_run'] is None


def test_busy_skip_or_retry(w):
    w.busy = True
    on(when_busy='skip')
    r = ttv.run('manual', now=NOW)
    assert r['error'] == 'Trakt is busy' and '_retry_at' not in r
    on(when_busy='retry')
    r = ttv.run('manual', now=NOW)
    assert r['error'] == 'Trakt is busy'
    assert ttv.state().get('retry_at') == NOW + ttv.BUSY_RETRY_S


# ------------------------------------------------------------------ routes / minimal allowlist

@pytest.fixture
def api(w, monkeypatch, owner):  # noqa: F811
    ran = []
    monkeypatch.setattr(ttv, 'run', lambda kind='scheduled', now=None: ran.append(kind) or {})
    monkeypatch.setitem(ttv_routes._start, 'fn', lambda target: target())
    monkeypatch.setattr(ttv, 'tv_profiles', lambda: [{'id': 1, 'name': 'HD-1080p', 'default': True}])
    owner.ran = ran
    return owner


def test_allowlisted_in_minimal_mode():
    from app import minimal
    assert minimal.path_allowed(API, 'GET') and minimal.path_allowed(API, 'PUT')
    for sub in ('/status', '/run'):
        assert minimal.path_allowed(API + sub, 'POST')
    assert minimal.path_allowed('/api/automation/trending-tv-watchlist/status', 'GET')


def test_get_put_and_run(api):
    r = _req(api, 'get', API)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    d = r.get_json()
    assert d['settings']['enabled'] is True and d['settings']['limit'] == 10
    assert d['libraries'][0]['name'] == 'TV' and d['monitor'][0] == 'all'
    assert 'en' in d['languages'] and d['history'] == []
    assert d['profiles'][0]['name'] == 'HD-1080p'
    r = _req(api, 'put', API, {'limit': 8, 'one_season_only': True})
    assert r.status_code == 200 and r.get_json()['settings']['limit'] == 8
    assert r.get_json()['status']['next_run']
    r = _req(api, 'put', API, {'limit': 99})
    assert r.status_code == 400 and r.get_json()['errors'] == {'limit': 'Invalid'}
    assert _req(api, 'post', API + '/run').status_code == 202 and api.ran == ['manual']
    assert _req(api, 'get', API + '/status').status_code == 200


def test_run_while_running_is_409(api, monkeypatch):
    monkeypatch.setattr(ttv, 'running', lambda: True)
    assert _req(api, 'post', API + '/run').status_code == 409
