"""v185 (PM): turning a bookmark ON searches an episode whose file is missing from disk now, even when an
earlier grab imported it (the file state wins over grab history). Automatic paths (feed, after-air) keep
treating an imported grab as done, so a deliberately deleted file is never re-grabbed on its own."""
import json
import time

import pytest

from app import tv_downloads, tv_search, tv_store
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_tv_search_v158b import allow_grabs, env, item  # noqa: F401
from tests.test_v182_series_detail import _req
from tests.test_v184_monitor_search import _no_files, _off
from tests.tv_fake import fake_tmdb  # noqa: F401

REL = 'Severance.S02E03.1080p.WEB-DL-X'


@pytest.fixture
def gone(env, monkeypatch):  # noqa: F811
    """S02E03 was grabbed and imported earlier, then its file was deleted (no link now)."""
    monkeypatch.setattr(tv_search, 'MS_REFILL_S', 0.0)
    monkeypatch.setattr(tv_search, 'BG_GAP_S', 0.0)
    allow_grabs(monkeypatch)
    _no_files(env.sid, 2)
    eid = env.e2[3]
    with tv_store.write() as c:
        c.execute("INSERT INTO grabs (hash, series_id, season_number, episode_ids, release_title, origin, grabbed_at, state, finished_at)"
                  " VALUES (?, ?, 2, ?, ?, 'auto', 1, 'imported', 2)", ('c' * 40, env.sid, json.dumps([eid]), REL))
    env.eid = eid
    return env


def test_episode_on_searches_a_missing_file_even_after_an_imported_grab(owner, gone):
    _off(owner, gone.sid)
    gone.results = [item('Severance.S02E03.2160p.WEB-DL-Y'), item(REL)]
    r = _req(owner, 'put', f'/api/settings/tv/episodes/{gone.eid}/monitored', {'monitored': True})
    assert r.get_json()['search'] == {'started': 1}
    tv_search.drain()
    assert gone.sent == [('Severance S02E03', 'manual', True)]
    assert any(g['state'] == 'downloading' for g in rows('SELECT state FROM grabs WHERE series_id = ?', gone.sid))


def test_season_and_show_on_search_it_too(owner, gone):
    _off(owner, gone.sid, 2)
    r = _req(owner, 'put', f'/api/settings/tv/shows/{gone.sid}/seasons/2/monitored', {'monitored': True})
    assert gone.eid in {i for p in tv_search.plan(gone.sid, season=2) for i in p['ids']}
    assert r.get_json()['search']['started'] >= 1
    tv_search._ms_queue.clear()
    with tv_store.write() as c:
        c.execute("UPDATE series SET monitor = 'all' WHERE id = ?", (gone.sid,))
    _req(owner, 'put', f'/api/settings/tv/shows/{gone.sid}/monitored', {'monitored': False})
    r = _req(owner, 'put', f'/api/settings/tv/shows/{gone.sid}/monitored', {'monitored': True})
    assert gone.eid in {i for p in tv_search.plan(gone.sid) for i in p['ids']}
    assert set(r.get_json()['search']) & {'started', 'confirm'}


def test_the_same_release_again_reopens_its_grab_so_the_watcher_imports_it(owner, gone, monkeypatch):
    from app import tv_grab
    h = 'c' * 40
    monkeypatch.setattr(tv_grab, 'info_hash', lambda kind, payload: h)
    tv_downloads._save(h, state='imported', at=2)
    gone.results = [item(REL)]
    _off(owner, gone.sid)
    _req(owner, 'put', f'/api/settings/tv/episodes/{gone.eid}/monitored', {'monitored': True})
    tv_search.drain()
    g = rows('SELECT state, finished_at, origin FROM grabs WHERE hash = ?', h)[0]
    assert (g['state'], g['finished_at'], g['origin']) == ('downloading', None, 'manual')
    assert tv_downloads.status(h)['state'] == 'waiting'          # no longer done: the next pass imports it
    assert tv_downloads._no_import(h, REL) is None


def test_automatic_paths_treat_an_imported_grab_as_done(gone):
    with tv_store.connect() as c:
        assert gone.eid not in {e['id'] for e in tv_search.wanted(c, gone.sid)}
    out = tv_search.search_show(gone.sid, [gone.eid], origin='auto', now=time.time())
    assert out['wanted'] == 0 and gone.sent == []
    out = tv_search.search_show(gone.sid, [gone.eid], origin='manual', interactive=True, now=time.time())
    assert out['wanted'] == 0                                    # other manual paths keep the history unless asked
    assert not rows("SELECT 1 FROM grabs WHERE state = 'downloading'")
