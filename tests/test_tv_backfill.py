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




# ------------------------------------------------- episode images (live 10e7d24: show backdrops)

def _card(owner, start='2026-10-11'):  # noqa: F811
    eps = _get(owner, '/api/settings/tv/calendar', start=start, days='7').get_json()['episodes']
    return {(e['show'], e['season'], e['episode']): e for e in eps}


def test_a_show_read_before_its_episode_aired_gets_the_still_once_it_airs(owner, scanned, fake_tmdb, monkeypatch):  # noqa: F811
    """The root cause on live 10e7d24: TMDB adds a still only after an episode airs. The show was read
    (stills_at set) before that, so it wasn't asked again for 24h and the card showed the backdrop.
    Now a Calendar open re-reads a show whose aired episode has no image, the card gets the still, and
    the retry is bounded (no TMDB call on the next open)."""
    import time as _t
    from app import tv_tmdb
    from tests import tv_fake
    monkeypatch.setattr(tv_scan, '_RefreshThread', _Now)
    sid = rows('SELECT id FROM series WHERE tmdb_id = 70523')[0]['id']
    now = _t.time()
    # read 2 hours before Dark S01E01 aired (2026-10-15 00:00 UTC) and refreshed recently: not stale
    aired_at = 1_792_108_800.0                                # aired 2026-10-15 (no airstamp): end of that day, UTC
    write('UPDATE series SET stills_at = ?, tmdb_refreshed_at = ?, tvmaze_checked_at = ?',
          aired_at - 7200, now - 60, now - 60)
    write('UPDATE episodes SET still_path = NULL, tvdb_image = NULL')
    monkeypatch.setitem(tv_fake.SHOWS, 70523, {**tv_fake.SHOWS[70523], 'stills': {(1, 1): '/darkS01E01.jpg'}})
    tv_tmdb.prune_cache()
    first = _get(owner, '/api/settings/tv/calendar', start='2026-10-11', days='7').get_json()
    assert first['refreshing'] is True                        # the page asks again shortly
    card = _card(owner)[('Dark', 1, 1)]
    assert card['still_path'] == '/darkS01E01.jpg'           # the episode image, not the show backdrop
    assert rows('SELECT stills_at FROM series WHERE id = ?', sid)[0]['stills_at'] >= now - 5
    n = len(fake_tmdb.calls)
    _card(owner)
    assert len(fake_tmdb.calls) == n                          # done: no loop
    assert len([c for c in fake_tmdb.calls if c[:2] == ('show', 70523)]) <= 2   # scan + one repair read, not two


def test_an_aired_episode_without_any_image_is_retried_but_bounded(owner, scanned, fake_tmdb, monkeypatch):  # noqa: F811
    """Dark S01E01 aired 2026-10-15 and TMDB never gets a still: asked again after it aired, then at most
    every STILL_RETRY_S, and never again once a read lands STILL_GRACE_S after it aired."""
    dark = rows('SELECT id FROM series WHERE tmdb_id = 70523')[0]['id']
    aired_at = 1_792_108_800.0                                  # 2026-10-15, end of day UTC
    week = (tv_scan.dt.date(2026, 10, 11), tv_scan.dt.date(2026, 10, 18))
    write('UPDATE episodes SET still_path = NULL, tvdb_image = NULL')
    write('UPDATE series SET stills_at = ?', aired_at - 3600)                     # read before it aired
    assert dark in tv_scan.still_gaps(*week, now=aired_at + 3600)
    write('UPDATE series SET stills_at = ?', aired_at + 3600)                     # read after: no image yet
    assert dark not in tv_scan.still_gaps(*week, now=aired_at + 3600 + 60)        # not before STILL_RETRY_S
    assert dark in tv_scan.still_gaps(*week, now=aired_at + 3600 + tv_scan.STILL_RETRY_S)
    write('UPDATE series SET stills_at = ?', aired_at + tv_scan.STILL_GRACE_S)    # read after the grace: done
    assert dark not in tv_scan.still_gaps(*week, now=aired_at + 10 * tv_scan.STILL_GRACE_S)


def test_the_visible_week_gets_its_own_pass_while_a_full_refresh_runs(owner, scanned, fake_tmdb, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_scan, '_RefreshThread', _Now)
    seen = []
    monkeypatch.setattr(tv_scan, 'refresh_added', lambda **k: seen.append(('tmdb', k.get('only'))) or 0)
    monkeypatch.setattr(tv_airtimes, 'refresh', lambda **k: seen.append(('tvdb', k.get('only'))) or 0)
    assert tv_scan.refresh_on_open({7}) is True
    assert seen == [('tmdb', {7}), ('tvdb', {7}), ('tmdb', None), ('tvdb', None)]   # this week first, then the rest
    seen.clear()
    with tv_scan._refresh_lock:                               # the full pass is still busy
        assert tv_scan.refresh_on_open({8}) is True
    assert seen == [('tmdb', {8}), ('tvdb', {8})]
    r = _get(owner, '/api/settings/tv/calendar', start='2026-10-11', days='7').get_json()
    assert 'refreshing' in r


# ------------------------------------------------- downloaded (live: The Reluctant Vampire S01E03)

def _linked_episode():
    return rows("SELECT e.id, e.series_id, e.season_number, e.episode_number, s.title FROM episodes e"
                " JOIN series s ON s.id = e.series_id JOIN episode_file_links l ON l.episode_id = e.id"
                " JOIN episode_files f ON f.id = l.file_id WHERE f.state = 'linked' AND e.season_number > 0"
                " AND s.episode_order = 'tmdb' ORDER BY e.id LIMIT 1")[0]


def test_an_episode_airing_today_with_its_file_is_downloaded(owner, scanned, fake_tmdb, monkeypatch):  # noqa: F811
    """A file that landed before the air date: the card on the air day says downloaded (not upcoming)."""
    monkeypatch.setattr(tv_scan, 'refresh_on_open', lambda *a, **k: False)
    e = _linked_episode()
    write("UPDATE episodes SET air_date = '2026-10-18' WHERE id = ?", e['id'])
    card = _card(owner, '2026-10-18')[(e['title'], e['season_number'], e['episode_number'])]
    assert card['downloaded'] is True


def test_a_linked_file_whose_link_row_is_gone_still_counts(owner, scanned, fake_tmdb, monkeypatch):  # noqa: F811
    """The file is 'linked' and named for SxxEyy, but its link row points nowhere (the episode row was
    replaced, or the file sits on another row of the same TMDB show): still downloaded."""
    monkeypatch.setattr(tv_scan, 'refresh_on_open', lambda *a, **k: False)
    e = _linked_episode()
    write("UPDATE episodes SET air_date = '2026-10-18' WHERE id = ?", e['id'])
    write('DELETE FROM episode_file_links WHERE episode_id = ?', e['id'])
    card = _card(owner, '2026-10-18')[(e['title'], e['season_number'], e['episode_number'])]
    assert card['downloaded'] is True
    write("UPDATE episode_files SET state = 'mismatch' WHERE series_id = ?", e['series_id'])
    card = _card(owner, '2026-10-18')[(e['title'], e['season_number'], e['episode_number'])]
    assert card['downloaded'] is False                          # only linked files count
