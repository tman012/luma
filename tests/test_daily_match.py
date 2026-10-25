"""PM: date-named releases import by air date (Raw/Judy daily type, and standard shows like SmackDown);
a torrent refused under an older matcher is looked at once more; an SxxExx refused one (Time) is not."""
import json
import time

import pytest

from app import tv_downloads, tv_release, tv_store
from tests.test_tv_download_watch_v158a import H1, H2, dl, grab, _release_names  # noqa: F401
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

RAW = 'WWE Monday Night Raw 2026 10 19 1080p WEBRip h264-GRP'


@pytest.mark.parametrize('name', [RAW, 'WWE.Monday.Night.Raw.2026.10.19.1080p.WEBRip.h264-GRP',
                                  'WWE Monday Night Raw 2026-10-19 1080p WEBRip h264-GRP',
                                  'Judy.Justice.2026.10.19.720p.WEB.h264-GRP'])
def test_date_formats_parse_daily(name):
    r = tv_release.parse(name)
    assert r.kind == 'daily' and r.air_date == '2026-10-19' and r.season is None and not r.episodes
    assert tv_release.parse(RAW).title.lower() == 'wwe monday night raw'


def _sev(conn, kind='daily'):
    sid = conn.execute("SELECT id FROM series WHERE title = 'Severance'").fetchone()['id']
    with tv_store.write() as w:
        w.execute('UPDATE series SET series_type = ?, monitored = 1 WHERE id = ?', (kind, sid))
        w.execute('UPDATE episodes SET monitored = 1 WHERE series_id = ?', (sid,))
    return sid


def _date(sid, season, ep, day):
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET air_date = ? WHERE series_id = ? AND season_number = ? AND episode_number = ?',
                     (day, sid, season, ep))


def _ep_id(sid, season, ep):
    with tv_store.connect() as conn:
        return conn.execute('SELECT id FROM episodes WHERE series_id = ? AND season_number = ? AND episode_number = ?',
                            (sid, season, ep)).fetchone()['id']


def test_air_date_match_single_multi_and_none(dl):
    with tv_store.connect() as conn:
        sid = _sev(conn)
    _date(sid, 2, 3, '2031-01-02')
    assert tv_downloads._episodes(sid, 'X 2031 01 02 1080p.mkv', 'X 2031 01 02 1080p.mkv') == (2, [3])
    assert tv_downloads._episodes(sid, 'X.2031.01.02.mkv', 'X.2031.01.02.mkv') == (2, [3])
    assert tv_downloads._episodes(sid, 'show.mkv', 'X 2031-01-02 1080p/show.mkv') == (2, [3])   # dated folder
    _date(sid, 2, 4, '2031-01-02')
    assert tv_downloads._episodes(sid, 'X 2031 01 02.mkv', 'X 2031 01 02.mkv') == (2, [3, 4])     # same-day pair
    assert tv_downloads._episodes(sid, 'X 2031 01 01.mkv', 'X 2031 01 01.mkv') == (None, [])      # no off-by-one
    assert tv_downloads._episodes(sid, 'X 2026 02 30.mkv', 'X 2026 02 30.mkv') == (None, [])


def _seed(sid, h, name, eps, state='downloading'):
    with tv_store.write() as conn:
        conn.execute("INSERT INTO grabs (hash, series_id, episode_ids, release_title, origin, grabbed_at, state)"
                     " VALUES (?, ?, ?, ?, 'auto', ?, ?)", (h, sid, json.dumps(eps), name, time.time() - 3600, state))
    tv_store.set_meta('tvdl:' + h, json.dumps({'state': 'refused', 'error': tv_downloads.NO_EPISODES,
                                               'at': time.time() - 3000, 'name': name}))


def test_raw_shape_refused_daily_is_retried_and_imported_without_a_regrab(dl):
    with tv_store.connect() as conn:
        sid = _sev(conn)
    _date(sid, 2, 3, '2031-01-02')
    eid = _ep_id(sid, 2, 3)
    name = 'Severance 2031 01 02 1080p WEBRip h264-GRP'          # Raw's exact shape on a library show
    grab(dl, H1, name, [name + '.mkv'])
    dl.q.torrents[0]['state'] = 'stalledUP'
    _seed(sid, H1, name, [eid])
    out = dl.tick()
    assert out == {'checked': 1, 'imported': 1}
    assert tv_downloads.status(H1)['state'] == 'imported'
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'imported'
    assert all(m == 'GET' or u.endswith('/api/v2/auth/login') for m, u, _ in dl.q.calls)    # no grab, no qBit write
    assert dl.tick() == {'checked': 0, 'imported': 0}


def test_daily_no_match_stays_refused_and_is_not_retried_again(dl):
    with tv_store.connect() as conn:
        sid = _sev(conn)
    name = 'Severance 2031 01 05 1080p WEBRip h264-GRP'
    grab(dl, H1, name, [name + '.mkv'])
    _seed(sid, H1, name, [_ep_id(sid, 2, 3)])
    assert dl.tick()['imported'] == 0
    st = tv_downloads.status(H1)
    assert st['state'] == 'refused' and st['error'] == tv_downloads.NO_EPISODES and st['matcher'] == tv_downloads.MATCHER
    assert dl.tick() == {'checked': 0, 'imported': 0}                                   # once, not every pass


def test_standard_show_dated_release_matches_and_refused_is_retried(dl):
    """v278: standard (non-daily) show + dated name (no SxxExx) resolves/imports by air date; MATCHER retry."""
    with tv_store.connect() as conn:
        sid = _sev(conn, 'standard')
    _date(sid, 2, 3, '2031-01-02')
    assert tv_downloads._episodes(sid, 'X 2031 01 02.mkv', 'X 2031 01 02.mkv') == (2, [3])
    name = 'Severance 2031 01 02 1080p WEBRip h264-GRP'
    eid = _ep_id(sid, 2, 3)
    grab(dl, H1, name, [name + '.mkv'])
    dl.q.torrents[0]['state'] = 'stalledUP'
    _seed(sid, H1, name, [eid])
    # Seeded as refused under MATCHER=2 (NO_EPISODES); MATCHER=3 + dropped daily gate retries it.
    out = dl.tick()
    assert out == {'checked': 1, 'imported': 1}
    assert tv_downloads.status(H1)['state'] == 'imported'
    assert tv_downloads.status(H1)['episode_ids'] == [eid]
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'imported'
    assert dl.tick() == {'checked': 0, 'imported': 0}


def test_smackdown_shape_standard_dated_release_imports(dl):
    """WWE SmackDown Fri miss shape: standard series + dated release, no SxxExx."""
    with tv_store.connect() as conn:
        sid = _sev(conn, 'standard')
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET title = 'WWE SmackDown', aliases = '[]', year = NULL WHERE id = ?", (sid,))
        conn.execute("UPDATE episodes SET air_date = NULL WHERE series_id = ? AND air_date = '2026-10-23'", (sid,))
    # Fixture has S02E03, not S28E43 — air-date path only needs the date on one episode in one season.
    _date(sid, 2, 3, '2026-10-23')
    eid = _ep_id(sid, 2, 3)
    name = 'WWE SmackDown 2026 10 23 NF iNT 1080p WEB h265-HEEL'
    assert tv_release.parse(name).kind == 'daily' and tv_release.parse(name).air_date == '2026-10-23'
    assert tv_downloads._episodes(sid, name + '.mkv', name + '.mkv') == (2, [3])
    grab(dl, H1, name, [name + '.mkv'])
    dl.q.torrents[0]['state'] = 'stalledUP'
    _seed(sid, H1, name, [eid])
    out = dl.tick()
    assert out == {'checked': 1, 'imported': 1}, tv_downloads.status(H1)
    assert tv_downloads.status(H1)['episode_ids'] == [eid]
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'imported'


def test_migration_sets_date_named_shows_daily_only(dl):
    with tv_store.connect() as conn:
        sid = _sev(conn, 'standard')
        other = conn.execute("SELECT id FROM series WHERE id != ? ORDER BY id LIMIT 1", (sid,)).fetchone()['id']
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET series_type = 'standard' WHERE id = ?", (other,))
        conn.execute("INSERT INTO grabs (hash, series_id, release_title, origin, grabbed_at) VALUES (?, ?, ?, 'auto', 1)",
                     ('d' * 40, sid, 'Severance 2031 01 02 1080p WEBRip h264-GRP'))
        conn.execute("INSERT INTO grabs (hash, series_id, release_title, origin, grabbed_at) VALUES (?, ?, ?, 'auto', 1)",
                     ('e' * 40, other, 'Other.S01E01.1080p-GRP'))
        assert tv_store.daily_types(conn) == [sid]
        assert tv_store.daily_types(conn) == []                                          # idempotent
        conn.execute("UPDATE series SET title = 'WWE Monday Night Raw', series_type = 'standard' WHERE id = ?", (other,))
        assert tv_store.daily_types(conn) == [other]
    assert {r['series_type'] for r in rows('SELECT series_type FROM series WHERE id IN (?, ?)', sid, other)} == {'daily'}


def test_time_shape_non_daily_refused_is_left_untouched(dl):
    with tv_store.connect() as conn:
        sid = _sev(conn, 'standard')
    name = 'Severance.S02E03.1080p.WEB-DL-GRP'                     # Time's shape: SxxEyy, refused, open row
    grab(dl, H2, name, [name + '.mkv'])
    _seed(sid, H2, name, [_ep_id(sid, 2, 3)])
    before = tv_store.get_meta('tvdl:' + H2)
    assert dl.tick() == {'checked': 0, 'imported': 0}
    assert tv_store.get_meta('tvdl:' + H2) == before
    assert rows('SELECT state FROM grabs WHERE hash = ?', H2)[0]['state'] == 'downloading'


def test_raw_2026_10_19_imports_from_the_refused_download(dl):
    with tv_store.connect() as conn:
        sid = _sev(conn)
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET title = 'WWE Monday Night Raw', aliases = '[]', year = NULL WHERE id = ?", (sid,))
        conn.execute("UPDATE episodes SET air_date = NULL WHERE series_id = ? AND air_date = '2026-10-19'", (sid,))
    _date(sid, 2, 3, '2026-10-19')
    eid = _ep_id(sid, 2, 3)
    grab(dl, H1, RAW, [RAW + '.mkv'])
    dl.q.torrents[0]['state'] = 'stalledUP'
    _seed(sid, H1, RAW, [eid])
    assert tv_downloads._series_for(RAW), rows('SELECT title, path_id FROM series WHERE id = ?', sid)
    out = dl.tick()
    assert out == {'checked': 1, 'imported': 1}, (tv_downloads.status(H1), tv_downloads._episodes(sid, RAW + '.mkv', RAW + '/' + RAW + '.mkv'))
    assert tv_downloads.status(H1)['episode_ids'] == [eid]
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'imported'
