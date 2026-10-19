"""v184 (Taylor): turning a bookmark ON (the owner's session) searches what it monitors that has aired and
has no file. Episode: at once. Season: a pack first (skipped when an episode of the season has a file),
else each missing aired episode. Show: its Monitor option, then the same per season. Manual grabs. More
than 25 episode searches ask first. The bulk editor, Edit's Monitor option and unmonitor never search."""
import pytest

from app import tv_search, tv_store
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_tv_search_v158b import allow_grabs, env, item  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.tv_fake import fake_tmdb  # noqa: F401


@pytest.fixture
def ms(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_search, 'MS_REFILL_S', 0.0)
    monkeypatch.setattr(tv_search, 'BG_GAP_S', 0.0)
    allow_grabs(monkeypatch)
    return env


def _no_files(sid, season):
    with tv_store.write() as c:
        c.execute('DELETE FROM episode_file_links WHERE episode_id IN (SELECT id FROM episodes WHERE series_id = ? AND season_number = ?)', (sid, season))


def _off(owner, sid, season=None):
    with tv_store.write() as c:
        if season is None:
            c.execute('UPDATE episodes SET monitored = 0 WHERE series_id = ?', (sid,))
        else:
            c.execute('UPDATE episodes SET monitored = 0 WHERE series_id = ? AND season_number = ?', (sid, season))
        from app import tv_monitor
        tv_monitor.sync_seasons(c, sid)


def _grabs():
    return rows("SELECT release_title, origin, episode_ids FROM grabs ORDER BY grabbed_at")


def test_episode_on_searches_it_at_once(owner, ms):
    eid = ms.e2[3]
    _off(owner, ms.sid)
    ms.results = [item('Severance.S02E03.1080p.WEB-DL-X')]
    r = _req(owner, 'put', f'/api/settings/tv/episodes/{eid}/monitored', {'monitored': True})
    assert r.status_code == 200 and r.get_json()['search'] == {'started': 1}
    tv_search.drain()
    assert ms.sent == [('Severance S02E03', 'manual', True)]
    g = _grabs()
    assert len(g) == 1 and g[0]['origin'] == 'manual' and g[0]['release_title'] == 'Severance.S02E03.1080p.WEB-DL-X'


def test_season_on_grabs_a_pack_first(owner, ms):
    _no_files(ms.sid, 2)
    _off(owner, ms.sid, 2)
    ms.results = [item('Severance.S02.1080p.WEB-DL-PACK'), item('Severance.S02E03.1080p.WEB-DL-X')]
    r = _req(owner, 'put', f'/api/settings/tv/shows/{ms.sid}/seasons/2/monitored', {'monitored': True})
    n = r.get_json()['search']['started']
    assert n >= 2
    tv_search.drain()
    assert ms.sent == [('Severance S02', 'manual', True)]               # one pack query, no per-episode ones
    assert [g['release_title'] for g in _grabs()] == ['Severance.S02.1080p.WEB-DL-PACK']


def test_season_with_no_pack_searches_each_missing_episode(owner, ms):
    _no_files(ms.sid, 2)
    _off(owner, ms.sid, 2)
    ms.results = []
    r = _req(owner, 'put', f'/api/settings/tv/shows/{ms.sid}/seasons/2/monitored', {'monitored': True})
    n = r.get_json()['search']['started']
    tv_search.drain()
    assert ms.sent[0] == ('Severance S02', 'manual', True)
    assert len(ms.sent) == 1 + n and all(q.startswith('Severance S02E') for q, _, _ in ms.sent[1:])
    assert _grabs() == []


def test_a_partial_season_skips_the_pack(owner, ms):
    have = rows("SELECT COUNT(*) AS n FROM episode_file_links l JOIN episodes e ON e.id = l.episode_id"
                " WHERE e.series_id = ? AND e.season_number = 2", ms.sid)[0]['n']
    assert have >= 1                                                    # the fixture has an S02 file
    _off(owner, ms.sid, 2)
    ms.results = [item('Severance.S02.1080p.WEB-DL-PACK')]
    r = _req(owner, 'put', f'/api/settings/tv/shows/{ms.sid}/seasons/2/monitored', {'monitored': True})
    assert r.get_json()['search']['started'] >= 1
    tv_search.drain()
    assert ms.sent and all(q.startswith('Severance S02E') for q, _, _ in ms.sent)
    assert _grabs() == []                                               # the pack is never taken


def test_the_season_search_button_uses_the_same_rule(owner, ms):
    _no_files(ms.sid, 2)
    ms.results = [item('Severance.S02.1080p.WEB-DL-PACK')]
    r = _req(owner, 'post', f'/api/settings/tv/shows/{ms.sid}/search', {'season': 2})
    assert r.status_code == 200 and r.get_json()['picks'][0]['title'] == 'Severance.S02.1080p.WEB-DL-PACK'
    assert ms.sent == [('Severance S02', 'manual', True)]


def test_show_on_applies_future_and_searches_what_it_monitors(owner, ms):
    import datetime as dt
    with tv_store.write() as c:
        c.execute("UPDATE series SET monitor = 'future' WHERE id = ?", (ms.sid,))
    assert _req(owner, 'put', f'/api/settings/tv/shows/{ms.sid}/monitored', {'monitored': False}).get_json().get('search') is None
    assert ms.sent == []
    r = _req(owner, 'put', f'/api/settings/tv/shows/{ms.sid}/monitored', {'monitored': True})
    today = dt.date.today().isoformat()
    # Future: only episodes from today on are monitored again; the search covers just those that aired, missing
    want = rows("SELECT COUNT(*) AS n FROM episodes e WHERE series_id = ? AND monitored = 1 AND air_date <= ? AND NOT EXISTS"
                " (SELECT 1 FROM episode_file_links l WHERE l.episode_id = e.id)", ms.sid, today)[0]['n']
    assert rows("SELECT COUNT(*) AS n FROM episodes WHERE series_id = ? AND monitored = 1 AND air_date < ?", ms.sid, today)[0]['n'] == 0
    assert r.get_json()['search'] == {'started': want}
    tv_search.drain()
    assert len(ms.sent) <= want
    ms.sent.clear()
    with tv_store.write() as c:                                         # All: the aired missing ones, season by season
        c.execute("UPDATE series SET monitor = 'all' WHERE id = ?", (ms.sid,))
    _req(owner, 'put', f'/api/settings/tv/shows/{ms.sid}/monitored', {'monitored': False})
    r = _req(owner, 'put', f'/api/settings/tv/shows/{ms.sid}/monitored', {'monitored': True})
    started = r.get_json()['search']
    assert 'started' in started or 'confirm' in started
    if 'started' in started:
        tv_search.drain()
        seasons = [q for q, _, _ in ms.sent]
        assert len(seasons) == len(set(seasons))                        # no season searched twice


def test_more_than_25_asks_first_and_searches_nothing(owner, ms, monkeypatch):
    monkeypatch.setattr(tv_search, 'CONFIRM_OVER', 2)
    _no_files(ms.sid, 2)
    _off(owner, ms.sid, 2)
    r = _req(owner, 'put', f'/api/settings/tv/shows/{ms.sid}/seasons/2/monitored', {'monitored': True})
    ask = r.get_json()['search']
    assert set(ask) == {'confirm'} and ask['confirm'] > 2
    tv_search.drain()
    assert ms.sent == [] and tv_search._ms_queue == []
    assert rows('SELECT monitored FROM seasons WHERE series_id = ? AND season_number = 2', ms.sid)[0]['monitored'] == 1
    u = f'/api/settings/tv/shows/{ms.sid}/monitor-search'
    assert _req(owner, 'post', u, {'season': 2}).status_code == 400            # not confirmed
    r = _req(owner, 'post', u, {'season': 2, 'confirm': True})
    assert r.status_code == 200 and r.get_json() == {'started': ask['confirm']}
    tv_search.drain()
    assert ms.sent[0] == ('Severance S02', 'manual', True)


def test_bulk_edit_option_change_and_unmonitor_never_search(owner, ms):
    _no_files(ms.sid, 2)
    sid = ms.sid
    ids = [ms.e2[n] for n in (3, 4)]
    assert _req(owner, 'put', f'/api/settings/tv/shows/{sid}/episodes/monitored', {'episode_ids': ids, 'monitored': True}).status_code in (200, 404)
    assert _req(owner, 'put', '/api/settings/tv/shows/bulk', {'ids': [sid], 'changes': {'monitored': True}}).status_code in (200, 400, 404)
    assert _req(owner, 'put', f'/api/settings/tv/shows/{sid}', {'monitor': 'all'}).status_code in (200, 400, 404)
    for u in (f'/api/settings/tv/shows/{sid}/seasons/2/monitored', f'/api/settings/tv/episodes/{ids[0]}/monitored',
              f'/api/settings/tv/shows/{sid}/monitored'):
        assert _req(owner, 'put', u, {'monitored': False}).status_code == 200
    tv_search.drain()
    assert ms.sent == [] and tv_search._ms_queue == [] and _grabs() == []


def test_monitor_search_is_the_signed_in_owners(minimal_app, owner, ms):  # noqa: F811
    from tests.test_home_rails import _client
    u = f'/api/settings/tv/shows/{ms.sid}/monitor-search'
    assert _req(minimal_app.test_client(), 'post', u, {'confirm': True}).status_code == 401
    assert _req(_client(minimal_app, admin=False, name='plain'), 'post', u, {'confirm': True}).status_code == 403
    assert ms.sent == []
