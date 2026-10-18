"""Calendar open backfills stills and air times never fetched, whatever their
age (this week's shows first), once: a show with no images isn't asked again."""
from app import tv_airtimes, tv_scan
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_airtimes import Maze, SEV, write
from tests.test_tv_calendar import _Now, _get, aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def test_never_fetched_stills_backfill_on_open_once(owner, scanned, fake_tmdb, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_scan, '_RefreshThread', _Now)
    assert rows('SELECT COUNT(*) AS n FROM series WHERE stills_at IS NULL')[0]['n'] == 0      # a scan reads them
    write('UPDATE series SET stills_at = NULL')               # shows from before stills: fresh (24h) but never read
    order = []
    real = tv_scan.fetch_show
    monkeypatch.setattr(tv_scan, 'fetch_show', lambda tid, *a, **k: (order.append(tid), real(tid, *a, **k))[1])
    monkeypatch.setattr(tv_scan, 'WORKERS', 1)
    r = _get(owner, '/api/settings/tv/calendar', start='2026-10-11', days='7')
    assert r.status_code == 200
    week = {e['series_id'] for e in r.get_json()['episodes']}
    ids = {x['id']: x['tmdb_id'] for x in rows('SELECT id, tmdb_id FROM series')}
    assert set(order) == set(ids.values())                    # every never-read show, ignoring the 24h staleness
    assert set(order[:len(week)]) == {ids[i] for i in week}   # this week's shows first
    assert rows('SELECT COUNT(*) AS n FROM series WHERE stills_at IS NULL')[0]['n'] == 0
    order.clear()
    _get(owner, '/api/settings/tv/calendar', start='2026-10-11', days='7')
    assert order == []                                        # no images is still "fetched": no loop


def test_never_fetched_air_times_this_week_first(scanned):  # noqa: F811
    seen = []
    maze = Maze({1: SEV})
    real = maze.__call__

    def spy(path, params):
        seen.append(params.get('thetvdb') or params.get('imdb') or path)
        return real(path, params)
    tv_airtimes.configure(get=spy, sleep=lambda s: None)
    sev = rows('SELECT id FROM series WHERE tmdb_id = 95396')[0]['id']
    write('UPDATE series SET tvmaze_checked_at = 1.0')        # all asked long ago ...
    write('UPDATE series SET tvmaze_checked_at = NULL WHERE id = ?', sev)   # ... but Severance never
    assert tv_airtimes.refresh(now=2000.0, first=set()) == 1 # recent-enough rows wait; never-asked goes now
    tv_airtimes.forget()                                      # a TVDB key saved: all asked again
    seen.clear()
    dark = rows('SELECT id FROM series WHERE tmdb_id = 70523')[0]['id']
    tv_airtimes.refresh(now=3000.0, first={dark})
    assert seen[0] == 334824                                  # Dark (this week) first


