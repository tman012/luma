"""v182 (Taylor): the series detail page. GET .../detail reads only; Search and Interactive search are the
owner's (signed-in) and user-initiated, so their grabs are origin 'manual': they may go past the 24 h floor
and are never held to the daily ceiling, but an episode with a file is still refused (no upgrades), every grab
goes through tv_grab (luma-test-tv category and tag, the scoped add), and the automatic paths stay floored.
Monitor toggles never search."""
import json

import pytest
from flask import jsonify

from app import tracker_sources, tv_grab, tv_safety, tv_search, tv_settings, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_tv_search_v158b import _auto, allow_grabs, env, item  # noqa: F401
from tests.test_tv_v158c import known_token  # noqa: F401
from tests.test_v177_tv_safety import NOW, _fill, adds, air
from tests.tv_fake import fake_tmdb  # noqa: F401

TOKEN = '/api/trackers/release/' + 'x' * 20


def _req(c, method, u, body=None):
    return getattr(c, method)(u, json=body, environ_base=REMOTE, headers=HDR)


def _ia(c, sid, body):
    return _req(c, 'post', f'/api/settings/tv/shows/{sid}/interactive', body)


def _grab(c, sid, title, **extra):
    return _req(c, 'post', f'/api/settings/tv/shows/{sid}/interactive/grab', {'download_url': TOKEN, 'title': title, 'size': 123, **extra})


@pytest.fixture
def spies(monkeypatch):
    calls = []
    for mod, names in ((tv_search, ('search_show', 'manual', 'interactive', 'on_added', 'schedule', 'run_due', 'feed', 'tick',
                                    'query_trackers')), (tv_grab, ('grab',))):
        for n in names:
            real = getattr(mod, n)
            monkeypatch.setattr(mod, n, lambda *a, _n=n, _r=real, **k: calls.append(_n) or _r(*a, **k))
    return calls


@pytest.fixture
def frozen(monkeypatch):
    """Every route call runs at NOW (2027-01-15), months after Severance S02 aired: all of it is past the floor."""
    import time as _t
    monkeypatch.setattr(_t, 'time', lambda: NOW)


# ------------------------------------------------------------------ the page's data

def test_detail_has_the_header_seasons_newest_first_and_episode_states(owner, env):  # noqa: F811
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET network = 'Apple TV+', status = 'Returning Series' WHERE id = ?", (env.sid,))
        conn.execute('UPDATE episode_files SET size = 1000 WHERE series_id = ?', (env.sid,))
        conn.execute('UPDATE episodes SET air_date = ? WHERE id = ?', ('2099-01-01', env.e2[6]))
        # pinned (v185.1): the fake's later S2 dates sit on the real calendar, so on Oct 19, 2026 one more had
        # aired and the count drifted; past E07 stays unaired whatever today is
        conn.execute("UPDATE episodes SET air_date = '2099-01-01', airstamp = NULL WHERE series_id = ? AND season_number = 2 AND episode_number > 7",
                     (env.sid,))
    r = _req(owner, 'get', f'/api/settings/tv/shows/{env.sid}/detail')
    assert r.status_code == 200
    v = r.get_json()
    assert (v['title'], v['network'], v['status'], v['monitored']) == ('Severance', 'Apple TV+', 'Returning Series', True)
    for k in ('poster_path', 'year', 'profile_name', 'path', 'size', 'have', 'total', 'seasons', 'episodes'):
        assert k in v
    files = rows("SELECT COUNT(*) AS n FROM episode_files WHERE series_id = ? AND state = 'linked'", env.sid)[0]['n']
    assert v['size'] == 1000 * files
    assert [s['season'] for s in v['seasons']] == [2, 1]                          # newest first
    s2 = v['seasons'][0]
    assert (s2['have'], s2['total']) == (3, 6)       # v184: every aired regular episode, monitored or not (like the list)
    from app import tv_scan
    lst = [x for x in tv_scan.library_view()['shows'] if x['id'] == env.sid][0]
    assert (v['have'], v['total']) == (min(lst['have'], lst['aired']), lst['aired'])
    st = {e['episode']: e['state'] for e in v['episodes'] if e['season'] == 2}
    assert (st[3], st[5], st[6]) == ('missing', 'downloaded', 'unaired')
    e = next(e for e in v['episodes'] if e['id'] == env.e2[5])
    assert e['has_file'] and {'quality', 'airstamp', 'title', 'size'} <= set(e)
    assert _req(owner, 'get', '/api/settings/tv/shows/99999/detail').status_code == 404


def test_an_active_grab_reads_downloading(owner, env):  # noqa: F811
    with tv_store.write() as conn:
        conn.execute("INSERT INTO grabs (hash, series_id, episode_ids, origin, grabbed_at, state) VALUES ('h1', ?, ?, 'manual', 1, 'downloading')",
                     (env.sid, json.dumps([env.e2[3]])))
    v = _req(owner, 'get', f'/api/settings/tv/shows/{env.sid}/detail').get_json()
    assert next(e for e in v['episodes'] if e['id'] == env.e2[3])['state'] == 'downloading'


# ------------------------------------------------------------------ interactive search

def test_interactive_search_lists_releases_with_rejections_and_grabs_nothing(owner, env, monkeypatch, frozen):  # noqa: F811
    allow_grabs(monkeypatch)
    env.results = [item('Severance.S02E03.720p.HDTV.x264-A'), item('Severance.S02E03.1080p.WEB-DL-B'),
                   item('Severance.S02E05.1080p.WEB-DL-C'), item('Severance.S02.1080p.WEB-DL-PACK'),
                   item('Other.Show.S02E03.1080p.WEB-DL-D'), item('Severance.S02E03.1080p.WEB-DL-F', seeders=0)]
    r = _ia(owner, env.sid, {'season': 2})
    assert r.status_code == 200, r.get_data()
    out = r.get_json()
    assert env.sent == [('Severance S02', 'manual', True)]                        # interactive budget, purpose manual
    assert adds(env) == [] and rows('SELECT * FROM grabs') == []                 # lists only
    by = {x['title']: x for x in out['releases']}
    assert by['Severance.S02E03.1080p.WEB-DL-B']['grabbable'] and by['Severance.S02E03.1080p.WEB-DL-B']['rejections'] == []
    assert by['Severance.S02E05.1080p.WEB-DL-C']['rejections'] == [tv_search.HAS_FILE] and not by['Severance.S02E05.1080p.WEB-DL-C']['grabbable']
    assert tv_search.HAS_FILE in by['Severance.S02.1080p.WEB-DL-PACK']['rejections']             # a pack over a file
    assert by['Other.Show.S02E03.1080p.WEB-DL-D']['rejections'][0] == tv_search.WRONG_SHOW
    assert by['Severance.S02E03.1080p.WEB-DL-F']['rejections'] == [tv_search.FEW_SEEDERS]
    assert by['Severance.S02E03.1080p.WEB-DL-F']['grabbable']                      # a profile rejection: the owner may override
    assert out['releases'][0]['title'] == 'Severance.S02E03.1080p.WEB-DL-B'      # the best first
    assert by['Severance.S02E03.1080p.WEB-DL-B']['download_url'] == TOKEN
    assert [e['episode'] for e in by['Severance.S02E03.1080p.WEB-DL-B']['episodes']] == [3]
    env.sent.clear()
    assert _ia(owner, env.sid, {'episode_id': env.e2[4]}).status_code == 200
    assert env.sent == [('Severance S02E04', 'manual', True)]


def test_interactive_grab_may_go_past_the_floor_with_the_tv_category_and_tag(owner, env, monkeypatch, known_token, frozen):  # noqa: F811
    allow_grabs(monkeypatch)
    air(env.e2[3], NOW - 60 * 86400)                                             # aired two months ago
    r = _grab(owner, env.sid, 'Severance.S02E03.1080p.WEB-DL-B', indexer='MyTracker', quality='1080p')
    assert r.status_code == 200 and r.get_json()['ok'] is True, r.get_json()
    (url, data, _), = adds(env)
    assert data['category'] == data['tags'] == 'luma-test-tv'
    g = rows('SELECT * FROM grabs')[0]
    assert (g['series_id'], g['origin'], json.loads(g['episode_ids'])) == (env.sid, 'manual', [env.e2[3]])


def test_interactive_grab_is_never_held_to_the_daily_ceiling(owner, env, monkeypatch, known_token, frozen):  # noqa: F811
    allow_grabs(monkeypatch)
    _fill(tv_safety.DAILY_CAP)                                                   # 30 automatic grabs today
    air(env.e2[4], NOW - 3600)
    assert tv_grab.grab(item('Severance.S02E04.1080p.WEB-DL-B'), env.sid, 2, [env.e2[4]], 'auto', NOW)['refused'] == tv_safety.CAP
    assert _grab(owner, env.sid, 'Severance.S02E04.1080p.WEB-DL-B').get_json()['ok'] is True


def test_an_episode_with_a_file_is_still_refused(owner, env, monkeypatch, known_token, frozen):  # noqa: F811
    allow_grabs(monkeypatch)
    for title in ('Severance.S02E05.2160p.WEB-DL-B', 'Severance.S02.1080p.WEB-DL-PACK'):
        out = _grab(owner, env.sid, title).get_json()
        assert out['ok'] is False and out['refused'] == tv_safety.HAS_FILE, title
    assert adds(env) == [] and rows('SELECT * FROM grabs') == []


def test_the_manual_grab_counts_toward_the_tracker_tally_without_being_automatic(env, monkeypatch, frozen):  # noqa: F811
    """Through tv_grab's own fetch: a manual grab is fetched as not automatic (tracker_sources then charges it
    to the interactive budget and notes it on the automation tally, v173: counted, never blocked)."""
    allow_grabs(monkeypatch)
    seen = []
    monkeypatch.setattr(tracker_sources, 'fetch_release', lambda tok, automatic=False: seen.append(automatic) or ('torrent', b'd4:infod4:name1:aee'))
    import inspect
    real = inspect.getclosurevars(tv_grab.grab).nonlocals['real']             # env's wrapper: the real grab, its own fetch
    monkeypatch.setattr(tv_grab, 'grab', lambda it, sid, season, ids, origin, now=None, **k:
                        real(it, sid, season, ids, origin, now, session_factory=env.q.factory, **k))
    out = tv_search.grab_release(env.sid, item('Severance.S02E03.1080p.WEB-DL-B'), now=NOW)
    assert out['ok'] and seen == [False], out
    assert len(adds(env)) == 1


def test_unparseable_or_other_show_releases_are_400(owner, env, monkeypatch, known_token, frozen):  # noqa: F811
    allow_grabs(monkeypatch)
    assert _grab(owner, env.sid, 'Other.Show.S02E03.1080p.WEB-DL-D').status_code == 400
    assert _grab(owner, env.sid, 'Severance.S09E01.1080p.WEB-DL-D').status_code == 400
    assert adds(env) == []


def test_grab_with_grabs_off_sends_nothing(owner, env, known_token, frozen):  # noqa: F811
    out = _grab(owner, env.sid, 'Severance.S02E03.1080p.WEB-DL-B').get_json()
    assert out == {'ok': False, 'error': tv_grab.OFF, 'blocked': True} and adds(env) == []


# ------------------------------------------------------------------ the automatic paths stay floored

def test_after_manual_grabs_the_automatic_paths_are_still_floored(owner, env, monkeypatch, known_token, frozen):  # noqa: F811
    allow_grabs(monkeypatch)
    _auto()
    for n in (3, 4, 6):
        air(env.e2[n], NOW - 10 * 86400)                                         # all old
    assert _grab(owner, env.sid, 'Severance.S02E03.1080p.WEB-DL-B').get_json()['ok'] is True
    n_adds = len(adds(env))
    # a direct automatic grab of an old episode: refused by the floor
    assert tv_grab.grab(item('Severance.S02E04.1080p.WEB-DL-B'), env.sid, 2, [env.e2[4]], 'auto', NOW)['refused'] == tv_safety.OLD
    # an automatic search asks for nothing past the floor
    env.results = [item('Severance.S02E04.1080p.WEB-DL-B'), item('Severance.S02E06.1080p.WEB-DL-B')]
    env.sent.clear()
    out = tv_search.search_show(env.sid, origin='auto', now=NOW)
    assert out['wanted'] == 0 and env.sent == []
    # the scheduler queues no backlog and a due entry past the floor is dropped
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = NULL')
    tv_search.schedule(NOW)
    assert rows('SELECT COUNT(*) AS n FROM episodes WHERE next_search_at IS NOT NULL')[0]['n'] == 0
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = ? WHERE id = ?', (NOW - 60, env.e2[4]))
    assert tv_search.run_due(NOW) == {'shows': 0, 'grabbed': 0}
    # on Add: only in-floor episodes queue
    assert tv_search.on_added(env.sid, NOW) == 0
    assert len(adds(env)) == n_adds and rows("SELECT COUNT(*) AS n FROM grabs WHERE origin = 'auto'")[0]['n'] == 0


def test_monitor_toggles_on_the_page_never_search(owner, env, spies, monkeypatch):  # noqa: F811
    allow_grabs(monkeypatch)
    _auto()
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET next_search_at = NULL')
    sid, e = env.sid, env.e2
    for u, b in ((f'/api/settings/tv/shows/{sid}/monitored', {'monitored': False}), (f'/api/settings/tv/shows/{sid}/monitored', {'monitored': True}),
                 (f'/api/settings/tv/shows/{sid}/seasons/1/monitored', {'monitored': True}),
                 (f'/api/settings/tv/shows/{sid}/seasons/2/monitored', {'monitored': False}),
                 (f'/api/settings/tv/episodes/{e[7]}/monitored', {'monitored': True}),
                 (f'/api/settings/tv/shows/{sid}/episodes/monitored', {'episode_ids': list(e.values()), 'monitored': True})):
        assert _req(owner, 'put', u, b).status_code == 200, u
    assert spies == [] and env.sent == [] and adds(env) == []
    assert rows('SELECT COUNT(*) AS n FROM episodes WHERE next_search_at IS NOT NULL')[0]['n'] == 0


# ------------------------------------------------------------------ owner only, validation, prod builds

def test_search_and_grab_are_the_signed_in_owners(minimal_app, owner, env, monkeypatch, known_token):  # noqa: F811
    allow_grabs(monkeypatch)
    sid = env.sid
    anon = minimal_app.test_client()
    others = (_client(minimal_app, name='second'), _client(minimal_app, admin=False, name='plain'))
    for u, b in ((f'/api/settings/tv/shows/{sid}/interactive', {'season': 2}), (f'/api/settings/tv/shows/{sid}/search', {'season': 2}),
                 (f'/api/settings/tv/shows/{sid}/interactive/grab', {'download_url': TOKEN, 'title': 'Severance.S02E03.1080p.WEB-DL-B'})):
        assert _req(anon, 'post', u, b).status_code == 401, u
        for c in others:
            assert _req(c, 'post', u, b).status_code == 403, u
    monkeypatch.setitem(tv_settings._cfg, 'signed_in_owner', lambda: (None, (jsonify({'error': 'Sign in', 'sign_in': True}), 401)))
    assert _ia(owner, sid, {'season': 2}).status_code == 401
    assert _grab(owner, sid, 'Severance.S02E03.1080p.WEB-DL-B').status_code == 401
    assert env.sent == [] and adds(env) == []


@pytest.mark.parametrize('body', [None, {}, {'season': 2, 'episode_id': 1}, {'season': True}, {'season': -1}, {'season': '2'},
                                  {'episode_id': 1.5}, {'season': 2, 'grab': True}])
def test_interactive_validates_the_body(owner, env, body):  # noqa: F811
    assert _ia(owner, env.sid, body).status_code == 400 and env.sent == []


@pytest.mark.parametrize('body', [None, {}, {'title': 'Severance.S02E03.1080p.WEB-DL-B'}, {'download_url': TOKEN},
                                  {'download_url': TOKEN, 'title': ''}, {'download_url': TOKEN, 'title': 'x', 'size': -1},
                                  {'download_url': TOKEN, 'title': 'x', 'size': True}, {'download_url': TOKEN, 'title': 'x', 'episode_ids': [1]},
                                  {'download_url': 'magnet:?xt=urn:btih:' + 'a' * 40, 'title': 'Severance.S02E03.1080p.WEB-DL-B'},
                                  {'download_url': 'https://tracker.example/dl/1?passkey=1', 'title': 'Severance.S02E03.1080p.WEB-DL-B'},
                                  {'download_url': '/api/trackers/release/' + 'y' * 20, 'title': 'Severance.S02E03.1080p.WEB-DL-B'}])
def test_interactive_grab_validates_the_body(owner, env, monkeypatch, known_token, body):  # noqa: F811
    allow_grabs(monkeypatch)
    assert _req(owner, 'post', f'/api/settings/tv/shows/{env.sid}/interactive/grab', body).status_code == 400
    assert adds(env) == []


def test_unknown_show_is_404(owner, env, monkeypatch, known_token):  # noqa: F811
    allow_grabs(monkeypatch)
    assert _ia(owner, 99999, {'season': 1}).status_code == 404
    assert _ia(owner, env.sid, {'season': 40}).status_code == 404
    assert _grab(owner, 99999, 'Severance.S02E03.1080p.WEB-DL-B').status_code == 404


def test_routes_are_404_on_prod_builds(test_app, monkeypatch):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    c = _client(test_app)
    assert _req(c, 'get', '/api/settings/tv/shows/1/detail').status_code == 404
    assert _req(c, 'post', '/api/settings/tv/shows/1/interactive', {'season': 1}).status_code == 404
    assert _req(c, 'post', '/api/settings/tv/shows/1/interactive/grab', {'download_url': TOKEN, 'title': 'x'}).status_code == 404


def test_add_series_search_and_add_are_the_signed_in_owners(minimal_app, owner, tv_env, fake_tmdb, monkeypatch):  # noqa: F811
    """v182: adding is the Series page's own Add series flow, owner-only (the toolbar no longer searches)."""
    anon = minimal_app.test_client()
    others = (_client(minimal_app, name='second'), _client(minimal_app, admin=False, name='plain'))
    for method, u, b in (('get', '/api/settings/tv/find?q=Severance', None), ('post', '/api/settings/tv/shows', {'tmdb_id': 6001})):
        assert _req(anon, method, u, b).status_code == 401, u
        for c in others:
            assert _req(c, method, u, b).status_code == 403, u
    with tv_store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM series').fetchone()[0] == 0
    assert _req(owner, 'get', '/api/settings/tv/find?q=Severance').status_code == 200
    monkeypatch.setitem(tv_settings._cfg, 'signed_in_owner', lambda: (None, (jsonify({'error': 'Sign in', 'sign_in': True}), 401)))
    assert _req(owner, 'get', '/api/settings/tv/find?q=Severance').status_code == 401
    assert _req(owner, 'post', '/api/settings/tv/shows', {'tmdb_id': 6001}).status_code == 401
