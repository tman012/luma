"""Calendar open backfills stills and air times never fetched, whatever their
age (this week's shows first), once: a show with no images isn't asked again."""
from app import tv_airtimes, tv_scan
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_airtimes import Maze, SEV, write
from tests.test_tv_calendar import _Now, _get, aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def test_calendar_open_never_reads_shows_again_for_stills(owner, scanned, fake_tmdb, monkeypatch):  # noqa: F811
    """v178 (Taylor): text-only calendar cards. A Calendar open no longer reads fresh shows again for
    episode images (never read, or an aired episode without one), and the answer has no image fields."""
    monkeypatch.setattr(tv_scan, '_RefreshThread', _Now)
    import time as _t
    write('UPDATE series SET stills_at = NULL, tmdb_refreshed_at = ?', _t.time() - 60)   # never read for stills, but fresh
    write('UPDATE episodes SET still_path = NULL, tvdb_image = NULL')
    order = []
    monkeypatch.setattr(tv_scan, 'fetch_show', lambda tid, *a, **k: order.append(tid))
    r = _get(owner, '/api/settings/tv/calendar', start='2026-10-11', days='7')
    assert r.status_code == 200
    assert order == []
    for e in r.get_json()['episodes']:
        assert not {'still_path', 'tvdb_image', 'backdrop_path'} & set(e)
    assert not hasattr(tv_scan, 'still_gaps')


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


def test_downloaded_is_only_a_link_row_to_this_exact_episode(owner, scanned, fake_tmdb, monkeypatch):  # noqa: F811
    """One strict rule (PM, v157a): a linked file whose link row points at this episode. A file that
    merely names the same SxxEyy with no link row doesn't count (the v156 fallback is gone)."""
    monkeypatch.setattr(tv_scan, 'refresh_on_open', lambda *a, **k: False)
    e = _linked_episode()
    write("UPDATE episodes SET air_date = '2026-10-18' WHERE id = ?", e['id'])
    key = (e['title'], e['season_number'], e['episode_number'])
    assert _card(owner, '2026-10-18')[key]['downloaded'] is True
    write("UPDATE episode_files SET state = 'mismatch' WHERE series_id = ?", e['series_id'])
    assert _card(owner, '2026-10-18')[key]['downloaded'] is False                 # only linked files count
    write("UPDATE episode_files SET state = 'linked' WHERE series_id = ?", e['series_id'])
    write('DELETE FROM episode_file_links WHERE episode_id = ?', e['id'])
    assert _card(owner, '2026-10-18')[key]['downloaded'] is False                 # no link row: not downloaded
