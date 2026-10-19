"""v184 (Tester FF-21, live on :5002): 'Time S03E03' was auto-grabbed and imported ('TV import:
Time.S03E03.1080p.WEBRip.X264-DEADPOOL -> 1 file(s)') but its tv.db grabs row stayed 'downloading' with no
finished_at and no 'imported' history event: nothing after the import ever touched the grabs row.
Now the import finishes the row in the same pass, and reconcile() repairs rows left behind."""
import json
import time

import pytest

from app import tv_downloads, tv_monitor, tv_safety, tv_store, tv_watch
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_download_watch_v158a import H1, H2, dl, grab  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

NAME = 'Time.S03E03.1080p.WEBRip.X264-DEADPOOL'
TODAY = time.strftime('%Y-%m-%d')


@pytest.fixture(autouse=True)
def _release_names(monkeypatch):
    """v185: these tests are about the watch, not naming: keep the release's name and 'Season 0N'
    (tests/test_v185_naming.py covers the Sonarr-style names)."""
    from app import tv_naming
    monkeypatch.setattr(tv_downloads, '_naming', lambda: {**tv_naming.DEFAULTS, 'rename_episodes': False,
                                                          'season_folder': 'Season {season:00}'})


@pytest.fixture
def time_show(dl):
    """The show from the live bug: Time (2021), season 3, in the scanned TV library."""
    with tv_store.write() as conn:
        conn.execute("INSERT INTO series (tmdb_id, title, year, path_id, folder, monitored, added_at) VALUES (990001, 'Time', 2021, ?, 'Time (2021)', 1, 0)",
                     (PID,))
        sid = conn.execute("SELECT id FROM series WHERE tmdb_id = 990001").fetchone()['id']
        for n in (1, 2, 3, 4):
            conn.execute('INSERT INTO episodes (series_id, season_number, episode_number, title, air_date, monitored) VALUES (?, 3, ?, ?, ?, 1)',
                         (sid, n, f'Episode {n}', TODAY))
        tv_monitor.sync_seasons(conn, sid)
        eps = {r['episode_number']: r['id'] for r in conn.execute('SELECT id, episode_number FROM episodes WHERE series_id = ?', (sid,))}
    return sid, eps


def _grab_row(h, sid, ids, title=NAME, origin='auto', at=None):
    with tv_store.write() as conn:
        conn.execute("INSERT INTO grabs (hash, series_id, season_number, episode_ids, release_title, tracker_id, quality, size, origin, grabbed_at)"
                     " VALUES (?, ?, 3, ?, ?, 1, '1080p-web', 1, ?, ?)", (h, sid, json.dumps(ids), title, origin, at or time.time() - 90))


def _row(h):
    return rows('SELECT state, finished_at, results FROM grabs WHERE hash = ?', h)[0]


def _events(h):
    return [r for r in rows("SELECT * FROM history WHERE event = 'imported'") if json.loads(r['detail']).get('hash') == h]


def _state(sid, eid):
    return next(e['state'] for e in tv_monitor.detail_view(sid, today=TODAY)['episodes'] if e['id'] == eid)


def test_the_import_marks_the_grab_imported_with_finished_at_and_the_history_event(dl, time_show):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    grab(dl, H1, NAME, [NAME + '.mkv'])
    assert _state(sid, eps[3]) == 'downloading'
    with tv_store.connect() as conn:
        before = tv_safety.automatic_today(conn, time.time())
    now = time.time()
    assert dl.tick(now) == {'checked': 1, 'imported': 1}
    assert (dl.lib / 'Time (2021)' / 'Season 03' / (NAME + '.mkv')).is_file()
    r = _row(H1)
    assert (r['state'], r['finished_at']) == ('imported', now)
    assert json.loads(r['results']) == {'episode_ids': [eps[3]], 'files': 1, 'via': 'import'}
    ev = _events(H1)
    assert len(ev) == 1 and json.loads(ev[0]['episode_ids']) == [eps[3]] and ev[0]['series_id'] == sid
    assert json.loads(ev[0]['detail']) == {'hash': H1, 'title': NAME, 'files': 1, 'via': 'import', 'origin': 'auto', 'state': 'imported'}
    assert _state(sid, eps[3]) == 'downloaded'                                    # this pass, not after 60s
    # every later 60s pass: the torrent is never imported again, the row and the event don't change
    for k in range(3):
        assert dl.tick(now + 60 * (k + 1)) == {'checked': 0, 'imported': 0}
    assert _row(H1)['finished_at'] == now and len(_events(H1)) == 1
    with tv_store.connect() as conn:                                              # the 30/24h tally is by grabbed_at
        assert tv_safety.automatic_today(conn, time.time()) == before


def test_the_stuck_row_from_before_the_fix_is_repaired_on_the_next_pass_and_at_start(dl, time_show, monkeypatch):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    grab(dl, H1, NAME, [NAME + '.mkv'])
    real = tv_downloads.finish_grab
    monkeypatch.setattr(tv_downloads, 'finish_grab', lambda *a, **k: None)        # v183: the import didn't touch the row
    at = time.time()
    dl.tick(at)
    st = tv_downloads.status(H1)
    st.pop('episode_ids')                                                        # and the v183 record had no episode ids
    tv_store.set_meta(tv_downloads._key(H1), json.dumps(st))
    assert _row(H1)['state'] == 'downloading' and _row(H1)['finished_at'] is None and _events(H1) == []
    monkeypatch.setattr(tv_downloads, 'finish_grab', real)
    assert tv_downloads.reconcile(at + 500) == 1                                  # what start() and every pass run
    r = _row(H1)
    assert (r['state'], r['finished_at']) == ('imported', at)                     # finished when it was imported
    assert json.loads(r['results'])['via'] == 'reconciled'
    assert len(_events(H1)) == 1 and json.loads(_events(H1)[0]['episode_ids']) == [eps[3]]
    assert tv_downloads.reconcile(at + 600) == 0 and len(_events(H1)) == 1        # once
    assert dl.tick(at + 700) == {'checked': 0, 'imported': 0}


def test_a_pre_v184_record_closes_the_row_without_waiting_for_a_link(dl, time_show):
    """v185.1 (PM, FF-21 residual): was 'wait for its file's link'; that left the live Time row
    'downloading' forever once its file was deleted. The import record says imported: that stands."""
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    tv_downloads._save(H1, state='imported', at=123.0, files=[{'path': 'x'}], name=NAME)    # a pre-v184 record
    dl.tick()
    r = _row(H1)
    assert (r['state'], r['finished_at']) == ('imported', 123.0) and len(_events(H1)) == 1


def test_reconcile_from_linked_files_alone_and_leaves_real_downloads(dl, time_show):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    _grab_row(H2, sid, [eps[4]], title='Time.S03E04.1080p.WEBRip.X264-DEADPOOL')
    folder = dl.lib / 'Time (2021)' / 'Season 03'
    folder.mkdir(parents=True)
    f = folder / 'Time.S03E03.1080p.WEB-DL-OTHER.mkv'                            # its file came another way
    f.write_bytes(b'episode-bytes' * 10)
    assert tv_watch.link_path(str(f))
    assert tv_downloads.reconcile() == 1
    assert _row(H1)['state'] == 'imported' and _row(H2)['state'] == 'downloading'   # S03E04 has no file: still downloading
    assert _state(sid, eps[4]) == 'downloading' and _state(sid, eps[3]) == 'downloaded'


def test_a_hash_qbittorrent_reports_differently_is_matched_by_the_release_name(dl, time_show):
    sid, eps = time_show
    _grab_row('d' * 40, sid, [eps[3]])
    grab(dl, H1, NAME, [NAME + '.mkv'])
    dl.tick()
    assert _row('d' * 40)['state'] == 'imported'
    assert len(_events('d' * 40)) == 1 and _events(H1) == []


def test_a_grab_the_torrent_only_partly_covers_is_partial(dl, time_show):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3], eps[4]], title='Time.S03E03E04.1080p.WEBRip.X264-DEADPOOL')
    grab(dl, H1, 'Time.S03E03E04.1080p.WEBRip.X264-DEADPOOL', [NAME + '.mkv'])     # only E03 inside
    dl.tick()
    assert _row(H1)['state'] == 'partial' and json.loads(_row(H1)['results'])['episode_ids'] == [eps[3]]
    assert _state(sid, eps[3]) == 'downloaded' and _state(sid, eps[4]) == 'missing'   # partial isn't active


def test_an_import_with_no_grab_row_still_writes_one_history_event(dl, time_show):
    sid, eps = time_show
    grab(dl, H1, NAME, [NAME + '.mkv'])
    dl.tick()
    ev = _events(H1)
    assert len(ev) == 1 and ev[0]['series_id'] == sid and json.loads(ev[0]['episode_ids']) == [eps[3]]
    assert rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 0


# ---------------------------------------------------------------- v184 (Ops): S03E03's Season 03 folder deleted outside Luma

def test_a_deleted_file_is_missing_never_downloading_and_is_never_re_imported(dl, time_show):
    import shutil
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    grab(dl, H1, NAME, [NAME + '.mkv'])
    now = time.time()
    assert dl.tick(now) == {'checked': 1, 'imported': 1}
    season = dl.lib / 'Time (2021)' / 'Season 03'
    assert _state(sid, eps[3]) == 'downloaded'
    # the stuck row as on :5002 (back to downloading), then the folder deleted over the share
    with tv_store.write() as conn:
        conn.execute("UPDATE grabs SET state = 'downloading', finished_at = NULL WHERE hash = ?", (H1,))
    shutil.rmtree(season)
    tv_watch._link_shows(tv_watch._shows(), now + 30)
    assert _state(sid, eps[3]) == 'missing'                      # a stale grab state never drives the display
    gone = rows("SELECT episode_ids, detail FROM history WHERE event = 'deleted' AND series_id = ?", sid)
    assert len(gone) == 1 and json.loads(gone[0]['episode_ids']) == [eps[3]]
    assert json.loads(gone[0]['detail'])['rel_path'].endswith(NAME + '.mkv')
    # the complete torrent is still in qBittorrent: the next passes close the row and never import it again
    for k in range(3):
        assert dl.tick(now + 60 * (k + 1))['imported'] == 0
    assert not season.exists()
    assert _row(H1)['state'] == 'imported'
    assert _state(sid, eps[3]) == 'missing'
    tv_watch._link_shows(tv_watch._shows(), now + 300)            # no second 'deleted' event
    assert len(rows("SELECT 1 FROM history WHERE event = 'deleted' AND series_id = ?", sid)) == 1


def test_an_unreachable_show_folder_is_not_a_deletion(dl, time_show):
    import shutil
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    grab(dl, H1, NAME, [NAME + '.mkv'])
    dl.tick(time.time())
    show = dl.lib / 'Time (2021)'
    keep = dl.lib.parent / 'moved-away'
    shutil.move(str(show), str(keep))                              # the whole show folder gone (a share dropped)
    tv_watch._link_shows(tv_watch._shows(), time.time())
    assert rows("SELECT 1 FROM history WHERE event = 'deleted' AND series_id = ?", sid) == []


@pytest.mark.parametrize('rename2', [True, False])
def test_the_copy_partial_is_renamed_into_place_with_no_leftover(tmp_path, monkeypatch, rename2):
    import os
    from app import library_mover as mover
    root = tmp_path / 'downloads'
    root.mkdir()
    src = root / (NAME + '.mkv')
    src.write_bytes(b'v' * 4096)
    dest = tmp_path / 'lib' / 'Season 03'
    dest.mkdir(parents=True)
    unlinked = []
    real = mover._unlink_at
    monkeypatch.setattr(mover, '_unlink_at', lambda dfd, n: unlinked.append(n) or real(dfd, n))
    if not rename2:
        monkeypatch.setattr(mover, '_rename2', lambda *a: None)      # a filesystem without renameat2: the old path
    dfd = os.open(str(dest), os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert mover.transfer(str(src), dfd, str(dest), NAME + '.mkv', 'copy', None, os.path.realpath(str(root))) == 'copied'
    finally:
        os.close(dfd)
    assert sorted(os.listdir(dest)) == [NAME + '.mkv']               # no '.…luma-partial' left behind
    assert (dest / (NAME + '.mkv')).read_bytes() == b'v' * 4096
    if rename2:
        assert unlinked == []                                         # renamed, so nothing deleted (no recycle-bin copy)


def test_a_row_closed_by_hand_is_left_alone_and_never_re_imported(dl, time_show):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    with tv_store.write() as conn:                                 # Ops closes the row by hand; no import record
        conn.execute("UPDATE grabs SET state = 'imported', finished_at = 123 WHERE hash = ?", (H1,))
    grab(dl, H1, NAME, [NAME + '.mkv'])                            # the complete torrent is still in qBittorrent
    now = time.time()
    assert tv_downloads.reconcile(now) == 0
    for k in range(3):
        assert dl.tick(now + 60 * k)['imported'] == 0
    assert not (dl.lib / 'Time (2021)' / 'Season 03').exists()
    assert _row(H1)['state'] == 'imported' and _row(H1)['finished_at'] == 123
    assert _events(H1) == [] and tv_downloads.reconcile(now + 999) == 0
    assert (tv_downloads.status(H1) or {}).get('error') == tv_downloads.ALREADY_IMPORTED


def test_an_unmonitored_episode_is_not_imported_until_monitored_again(dl, time_show):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET monitored = 0 WHERE id = ?', (eps[3],))
    grab(dl, H1, NAME, [NAME + '.mkv'])
    now = time.time()
    for k in range(2):
        assert dl.tick(now + 60 * k)['imported'] == 0
    assert not (dl.lib / 'Time (2021)' / 'Season 03').exists() and _row(H1)['state'] == 'downloading'
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET monitored = 1 WHERE id = ?', (eps[3],))
    assert dl.tick(now + 180)['imported'] == 1
    assert (dl.lib / 'Time (2021)' / 'Season 03' / (NAME + '.mkv')).is_file() and _row(H1)['state'] == 'imported'


def test_a_closed_row_is_matched_by_hash_only(dl, time_show):
    """v185 (Reviewer b): another torrent with the same release name is never refused by a closed row."""
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    with tv_store.write() as conn:
        conn.execute("UPDATE grabs SET state = 'imported', finished_at = ? WHERE hash = ?", (time.time(), H1))
    assert tv_downloads._no_import(H1, NAME) == tv_downloads.ALREADY_IMPORTED
    assert tv_downloads._no_import('d' * 40, NAME) is None
    assert tv_downloads.finish_grab('d' * 40, NAME, sid, [eps[3]], 1, time.time()) is None
    assert _row(H1)['state'] == 'imported'


# ---------------------------------------------------------------- v185.1: the live Time S03E03 shape

PRE_V184 = {'state': 'imported', 'at': 1760922480.0, 'completed': 1760922300, 'error': None,
            'name': NAME, 'files': [{'path': '/media/TV/Time (2021)/Season 03/' + NAME + '.mkv', 'how': 'copied'}]}


@pytest.mark.parametrize('row_hash', [H1, H1.upper()])          # pre-v184 rows may keep the tracker's uppercase hash
def test_the_live_time_row_closes_with_its_file_deleted_and_the_episode_unmonitored(dl, time_show, row_hash):
    """Imported at 9:08 PM ET under a pre-v184 build (a record with no episode_ids), the file deleted on
    purpose later, the episode unmonitored, the grab row still 'downloading'."""
    sid, eps = time_show
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET monitored = 0 WHERE id = ?', (eps[3],))
    _grab_row(row_hash, sid, [eps[3]], at=PRE_V184['at'] - 600)
    tv_downloads._save(H1, **PRE_V184)
    assert not (dl.lib / 'Time (2021)' / 'Season 03' / (NAME + '.mkv')).exists()        # the file is gone
    files0 = sorted(p.name for p in dl.lib.rglob('*'))
    grabs0 = rows('SELECT COUNT(*) AS n FROM grabs')[0]['n']
    assert tv_downloads.reconcile(PRE_V184['at'] + 3600) == 1
    r = rows('SELECT state, finished_at, results FROM grabs WHERE hash = ?', row_hash)[0]
    assert (r['state'], r['finished_at']) == ('imported', PRE_V184['at'])
    assert json.loads(r['results'])['episode_ids'] == [eps[3]]
    ev = [e for e in rows("SELECT * FROM history WHERE event = 'imported'")
          if json.loads(e['detail']).get('hash') in (H1, row_hash)]
    assert len(ev) == 1
    assert tv_downloads.reconcile(PRE_V184['at'] + 7200) == 0                             # once
    assert sorted(p.name for p in dl.lib.rglob('*')) == files0                            # no file operations
    assert rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == grabs0                      # no new grab (no search)
    assert tv_downloads.status(H1)['state'] == 'imported'                                 # the record is not redone
    assert dl.tick(PRE_V184['at'] + 7300) == {'checked': 0, 'imported': 0}


@pytest.mark.parametrize('ids', ['["%d"]', '[{"id": %d}]', '%d'])
def test_older_grab_row_episode_id_shapes(dl, time_show, ids):
    sid, eps = time_show
    with tv_store.write() as conn:
        conn.execute("INSERT INTO grabs (hash, series_id, season_number, episode_ids, release_title, tracker_id, quality, size, origin, grabbed_at)"
                     " VALUES (?, ?, 3, ?, ?, 1, '1080p-web', 1, 'auto', ?)", (H1, sid, ids % eps[3], NAME, time.time() - 90))
    tv_downloads._save(H1, **PRE_V184)
    assert tv_downloads.reconcile() == 1 and _row(H1)['state'] == 'imported'


def test_a_record_found_by_its_release_name_when_the_row_hash_differs(dl, time_show):
    sid, eps = time_show
    _grab_row('f' * 40, sid, [eps[3]])
    tv_downloads._save(H1, **PRE_V184)
    assert tv_downloads.reconcile() == 1 and _row('f' * 40)['state'] == 'imported'


def test_no_record_and_no_file_stays_downloading(dl, time_show):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]])
    assert tv_downloads.reconcile() == 0 and _row(H1)['state'] == 'downloading'
