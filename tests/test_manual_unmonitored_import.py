"""An explicit manual grab imports when the episode or the show is unmonitored.

Monitoring still refuses every other completed torrent: automatic and upgrade grabs, and a category
torrent with no open manual row. The authorization is the grabs row Luma wrote (origin manual, still
open, with episode ids), which survives a process restart. A qBittorrent category or a release name is
not a request. Deleting that row, or leaving it failed, skipped, manual_needed, imported, or partial,
does not let the category import an unmonitored episode. A grab names the episodes it may place, so a
pack does not import siblings that were not requested.

A file already linked by the library watcher is reconciled by the normal pass: the grab closes because
those episodes are linked, the watcher does not copy again, and the ledger becomes refused / already
imported. That ledger is not an import record and does not list mover files.
"""
import json
import os
import time

import pytest

from app import tv_downloads, tv_grab, tv_monitor, tv_search, tv_store, tv_watch
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_download_watch_v158a import H1, H2, dl, grab  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.test_v184_grab_import import NAME, _grab_row, time_show  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

E1 = 'Time.S03E01.1080p.WEBRip.X264-DEADPOOL'
E4 = 'Time.S03E04.1080p.WEBRip.X264-DEADPOOL'
BOTH = 'Time.S03E03E04.1080p.WEBRip.X264-DEADPOOL'
PACK = 'Time.S03.1080p.WEBRip.X264-DEADPOOL'
LANDED = b'landed-by-recovery-not-the-importer'
OUTSIDE = b'outside-target-not-the-episode'


@pytest.fixture(autouse=True)
def _release_names(monkeypatch):
    from app import tv_naming
    monkeypatch.setattr(tv_downloads, '_naming', lambda: {**tv_naming.DEFAULTS, 'rename_episodes': False,
                                                          'season_folder': 'Season {season:00}'})
    # The external remote mover is not this machine. The watch still uses the local mover
    # (copy, no overwrite, source left in place). qBittorrent is the dl fixture's stub.
    monkeypatch.setenv('LUMA_REMOTE_MOVER', '0')


def _off(sid, eids, show=True):
    with tv_store.write() as conn:
        if not show:
            conn.execute('UPDATE series SET monitored = 0 WHERE id = ?', (sid,))
        if eids:
            conn.execute(f'UPDATE episodes SET monitored = 0 WHERE id IN ({",".join("?" * len(eids))})', tuple(eids))


def _policy(sid):
    with tv_store.connect() as conn:
        show = dict(conn.execute(
            'SELECT monitored, monitor, monitor_chosen, monitor_specials FROM series WHERE id = ?', (sid,)).fetchone())
        eps = [dict(r) for r in conn.execute(
            'SELECT id, monitored, search_attempts, last_search_at FROM episodes WHERE series_id = ? ORDER BY id', (sid,))]
        seasons = [dict(r) for r in conn.execute(
            'SELECT season_number, monitored FROM seasons WHERE series_id = ? ORDER BY season_number', (sid,))]
        searches = conn.execute('SELECT COUNT(*) AS n FROM search_log').fetchone()['n']
        grabs = conn.execute('SELECT COUNT(*) AS n FROM grabs').fetchone()['n']
    return show, eps, seasons, searches, grabs


def _forbid(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError('monitoring, search, or a new grab')
    for mod, name in ((tv_search, 'monitor_search'), (tv_search, 'search_show'), (tv_search, 'manual'),
                      (tv_monitor, 'set_episodes'), (tv_monitor, 'set_series'), (tv_monitor, 'set_season'),
                      (tv_grab, 'grab')):
        monkeypatch.setattr(mod, name, boom)


def _dest(dl, filename):
    return dl.lib / 'Time (2021)' / 'Season 03' / filename


def _under_show(path):
    show = os.path.realpath(str(path.parents[1] if path.parent.name.startswith('Season') else path.parent))
    return os.path.commonpath([os.path.realpath(str(path)), show]) == show


def _linked(eid):
    return rows('SELECT f.state, f.rel_path, f.size FROM episode_files f'
                ' JOIN episode_file_links l ON l.file_id = f.id WHERE l.episode_id = ?', eid)


def _events(h):
    return [r for r in rows("SELECT * FROM history WHERE event = 'imported'")
            if json.loads(r['detail']).get('hash') == h]


def _grabbed(h):
    return rows("SELECT * FROM history WHERE event = 'grabbed' AND detail LIKE ?", f'%{h}%')


def _src(dl, torrent_name, filename):
    return dl.root / torrent_name / filename


def _no_client_write(dl):
    assert not any('delete' in u or u.endswith('/torrents/add') for _m, u, _p in dl.q.calls)


# ---------------------------------------------------------------- the request is imported

def test_open_manual_imports_when_episode_and_show_are_unmonitored(dl, time_show, monkeypatch):
    sid, eps = time_show
    eid = eps[3]
    _off(sid, [eid], show=False)
    _grab_row(H1, sid, [eid], origin='manual')
    tv_downloads._save(H1, state='waiting', error=tv_downloads.UNMONITORED, at=time.time() - 30, name=NAME)
    src = _src(dl, NAME, NAME + '.mkv')
    grab(dl, H1, NAME, [NAME + '.mkv'])
    source = src.read_bytes()
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    assert dl.tick(now) == {'checked': 1, 'imported': 1}
    dest = _dest(dl, NAME + '.mkv')
    assert dest.is_file() and not dest.is_symlink() and _under_show(dest)
    assert dest.read_bytes() == source
    assert src.is_file() and not src.is_symlink() and src.read_bytes() == source
    link = _linked(eid)
    assert len(link) == 1 and link[0]['state'] == 'linked' and link[0]['size'] == dest.stat().st_size
    assert link[0]['rel_path'].endswith(NAME + '.mkv')
    row = rows('SELECT state, finished_at, origin, results FROM grabs WHERE hash = ?', H1)[0]
    assert row['state'] == 'imported' and row['origin'] == 'manual' and row['finished_at'] == now
    assert json.loads(row['results'])['via'] == 'import'
    ev = _events(H1)
    assert len(ev) == 1 and json.loads(ev[0]['detail'])['origin'] == 'manual'
    assert json.loads(ev[0]['episode_ids']) == [eid]
    assert _policy(sid) == policy
    assert _grabbed(H1) == []
    for k in range(3):
        assert dl.tick(now + 60 * (k + 1)) == {'checked': 0, 'imported': 0}
    assert dest.read_bytes() == source and src.read_bytes() == source
    assert len(_events(H1)) == 1 and rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 1
    assert _policy(sid) == policy
    _no_client_write(dl)


def test_manual_imports_when_only_the_show_is_unmonitored(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [], show=False)
    _grab_row(H1, sid, [eps[3]], origin='manual')
    grab(dl, H1, NAME, [NAME + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    assert dl.tick()['imported'] == 1
    assert _dest(dl, NAME + '.mkv').is_file()
    assert _linked(eps[3])[0]['state'] == 'linked'
    assert _policy(sid) == policy


@pytest.mark.parametrize('origin', ['auto', 'upgrade'])
def test_automatic_and_upgrade_stay_unimported_while_unmonitored(dl, time_show, monkeypatch, origin):
    sid, eps = time_show
    _off(sid, [eps[3]], show=True)
    _grab_row(H1, sid, [eps[3]], origin=origin)
    grab(dl, H1, NAME, [NAME + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    for k in range(2):
        assert dl.tick(now + 60 * k)['imported'] == 0
    assert not _dest(dl, NAME + '.mkv').exists()
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'downloading'
    assert (tv_downloads.status(H1) or {}).get('error') == tv_downloads.UNMONITORED
    assert _policy(sid) == policy and _events(H1) == []
    with tv_store.write() as conn:
        conn.execute('UPDATE episodes SET monitored = 1 WHERE id = ?', (eps[3],))
    assert dl.tick(now + 180)['imported'] == 1
    assert _dest(dl, NAME + '.mkv').is_file() and _linked(eps[3])[0]['state'] == 'linked'


def test_automatic_does_not_import_when_only_the_show_is_unmonitored(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [], show=False)
    _grab_row(H1, sid, [eps[3]], origin='auto')
    grab(dl, H1, NAME, [NAME + '.mkv'])
    _forbid(monkeypatch)
    assert dl.tick()['imported'] == 0
    assert not _dest(dl, NAME + '.mkv').exists()
    assert rows('SELECT monitored FROM series WHERE id = ?', sid)[0]['monitored'] == 0


# ---------------------------------------------------------------- not a request

def test_a_category_torrent_with_no_grab_does_not_import_an_unmonitored_episode(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [eps[3]], show=True)
    src = _src(dl, NAME, NAME + '.mkv')
    grab(dl, H1, NAME, [NAME + '.mkv'])
    source = src.read_bytes()
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    for k in range(3):
        assert dl.tick(now + 60 * k) == {'checked': 0, 'imported': 0}
    assert not _dest(dl, NAME + '.mkv').exists()
    assert src.read_bytes() == source
    assert rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 0
    st = tv_downloads.status(H1)
    assert st['state'] == 'waiting' and st['error'] == tv_downloads.UNMONITORED and 'files' not in st
    assert _events(H1) == [] and _policy(sid) == policy
    _no_client_write(dl)


def test_a_category_torrent_with_no_grab_does_not_import_when_the_show_is_unmonitored(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [], show=False)
    grab(dl, H1, NAME, [NAME + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    for k in range(2):
        assert dl.tick(now + 60 * k)['imported'] == 0
    assert not _dest(dl, NAME + '.mkv').exists()
    assert (tv_downloads.status(H1) or {}).get('error') == tv_downloads.UNMONITORED
    assert rows('SELECT monitored FROM episodes WHERE id = ?', eps[3])[0]['monitored'] == 1
    assert rows('SELECT monitored FROM series WHERE id = ?', sid)[0]['monitored'] == 0
    assert _policy(sid) == policy and _events(H1) == []


def test_a_monitored_category_torrent_with_no_grab_still_imports(dl, time_show, monkeypatch):
    """The episode and the show are monitored. The category watch files the torrent and does not
    record a manual grab."""
    sid, eps = time_show
    src = _src(dl, NAME, NAME + '.mkv')
    grab(dl, H1, NAME, [NAME + '.mkv'])
    source = src.read_bytes()
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    assert dl.tick(now) == {'checked': 1, 'imported': 1}
    dest = _dest(dl, NAME + '.mkv')
    assert dest.is_file() and not dest.is_symlink() and dest.read_bytes() == source
    assert src.is_file() and src.read_bytes() == source
    assert _linked(eps[3])[0]['state'] == 'linked'
    assert rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 0
    ev = _events(H1)
    assert len(ev) == 1 and 'origin' not in json.loads(ev[0]['detail'])
    assert _policy(sid) == policy
    assert dl.tick(now + 60) == {'checked': 0, 'imported': 0}
    assert dest.read_bytes() == source and len(_events(H1)) == 1


def test_a_category_pack_imports_only_the_monitored_episode(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [eps[3]])
    grab(dl, H1, BOTH, [NAME + '.mkv', E4 + '.mkv'])
    e3 = _src(dl, BOTH, NAME + '.mkv')
    e3bytes = e3.read_bytes()
    policy = _policy(sid)
    _forbid(monkeypatch)
    assert dl.tick()['imported'] == 1
    assert not _dest(dl, NAME + '.mkv').exists()
    assert _dest(dl, E4 + '.mkv').is_file()
    assert _linked(eps[3]) == [] and _linked(eps[4])[0]['state'] == 'linked'
    assert e3.read_bytes() == e3bytes
    assert rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 0
    assert _policy(sid) == policy


def test_a_name_that_says_manual_does_not_authorize_or_monitor(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [eps[3]])
    title = 'Time.S03E03.1080p.WEBRip.manual-DEADPOOL'
    grab(dl, H1, title, [title + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    for k in range(2):
        assert dl.tick(now + 60 * k)['imported'] == 0
    assert not _dest(dl, title + '.mkv').exists()
    assert rows("SELECT COUNT(*) AS n FROM grabs WHERE origin = 'manual'")[0]['n'] == 0
    assert (tv_downloads.status(H1) or {}).get('error') == tv_downloads.UNMONITORED
    assert _policy(sid) == policy


def test_a_manual_row_without_episode_ids_does_not_bypass_monitoring(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [eps[3]], show=False)
    _grab_row(H1, sid, [], origin='manual')
    grab(dl, H1, NAME, [NAME + '.mkv'])
    _forbid(monkeypatch)
    assert tv_downloads._no_import(H1, NAME) == tv_downloads.UNMONITORED
    assert dl.tick()['imported'] == 0
    assert not _dest(dl, NAME + '.mkv').exists()
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'downloading'


def test_an_open_automatic_row_is_not_a_manual_request(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [eps[3]])
    _grab_row(H1, sid, [eps[3]], origin='auto')
    grab(dl, H1, NAME, [NAME + '.mkv'], tags='luma-test-tv')
    _forbid(monkeypatch)
    assert tv_downloads._no_import(H1, NAME) == tv_downloads.UNMONITORED
    assert dl.tick()['imported'] == 0
    assert not _dest(dl, NAME + '.mkv').exists()


def test_a_different_hash_does_not_borrow_a_closed_manual_row(dl, time_show, monkeypatch):
    """A closed row matches by hash only. Another completed torrent of the same name is not that
    request. With the episode unmonitored, the category does not import it either."""
    sid, eps = time_show
    _off(sid, [eps[3]])
    _grab_row(H1, sid, [eps[3]], origin='manual')
    with tv_store.write() as conn:
        conn.execute("UPDATE grabs SET state = 'imported', finished_at = 5 WHERE hash = ?", (H1,))
    grab(dl, H2, NAME, [NAME + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    for k in range(2):
        assert dl.tick(now + 60 * k)['imported'] == 0
    assert not _dest(dl, NAME + '.mkv').exists()
    assert rows('SELECT state, finished_at FROM grabs WHERE hash = ?', H1)[0]['finished_at'] == 5
    assert (tv_downloads.status(H2) or {}).get('error') == tv_downloads.UNMONITORED
    assert _events(H2) == [] and _policy(sid) == policy


def test_deleting_the_grab_row_does_not_let_the_category_import_an_unmonitored_episode(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [eps[3]], show=False)
    _grab_row(H1, sid, [eps[3]], origin='manual')
    with tv_store.write() as conn:
        conn.execute('DELETE FROM grabs WHERE hash = ?', (H1,))
    grab(dl, H1, NAME, [NAME + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    for k in range(3):
        assert dl.tick(now + 60 * k) == {'checked': 0, 'imported': 0}
    assert not _dest(dl, NAME + '.mkv').exists()
    assert rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 0
    assert (tv_downloads.status(H1) or {}).get('state') == 'waiting'
    assert (tv_downloads.status(H1) or {}).get('error') == tv_downloads.UNMONITORED
    assert _policy(sid) == policy and _events(H1) == []


@pytest.mark.parametrize('state', ['failed', 'skipped', 'manual_needed', 'imported', 'partial'])
def test_a_closed_row_is_not_reopened_by_the_category_when_the_episode_is_monitored(dl, time_show, monkeypatch, state):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]], origin='manual')
    with tv_store.write() as conn:
        conn.execute('UPDATE grabs SET state = ?, finished_at = 5, error = ? WHERE hash = ?',
                     (state, 'stopped', H1))
    grab(dl, H1, NAME, [NAME + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    for k in range(2):
        assert dl.tick(now + 60 * k)['imported'] == 0
    assert not _dest(dl, NAME + '.mkv').exists()
    row = rows('SELECT state, finished_at, error FROM grabs WHERE hash = ?', H1)[0]
    assert (row['state'], row['finished_at'], row['error']) == (state, 5, 'stopped')
    assert (tv_downloads.status(H1) or {}).get('error') == tv_downloads.ALREADY_IMPORTED
    assert _events(H1) == [] and _policy(sid) == policy


def test_hash_mismatch_still_uses_the_open_manual_row(dl, time_show, monkeypatch):
    """qBittorrent's hash can differ from the row. The open row is found by its exact release name."""
    sid, eps = time_show
    _off(sid, [eps[3]])
    _grab_row('d' * 40, sid, [eps[3]], origin='manual')
    grab(dl, H1, NAME, [NAME + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    assert dl.tick()['imported'] == 1
    assert rows('SELECT state FROM grabs WHERE hash = ?', 'd' * 40)[0]['state'] == 'imported'
    assert _linked(eps[3])[0]['state'] == 'linked'
    assert _policy(sid) == policy


# ---------------------------------------------------------------- cancellation, deletion, packs

@pytest.mark.parametrize('state', ['imported', 'partial', 'failed', 'skipped', 'manual_needed'])
def test_a_closed_manual_row_is_not_imported_again(dl, time_show, monkeypatch, state):
    sid, eps = time_show
    _off(sid, [eps[3]], show=False)
    _grab_row(H1, sid, [eps[3]], origin='manual')
    with tv_store.write() as conn:
        conn.execute('UPDATE grabs SET state = ?, finished_at = 5, error = ? WHERE hash = ?',
                     (state, 'stopped', H1))
    grab(dl, H1, NAME, [NAME + '.mkv'])
    _forbid(monkeypatch)
    now = time.time()
    for k in range(2):
        assert dl.tick(now + 60 * k)['imported'] == 0
    assert not _dest(dl, NAME + '.mkv').exists()
    row = rows('SELECT state, finished_at, error FROM grabs WHERE hash = ?', H1)[0]
    assert (row['state'], row['finished_at'], row['error']) == (state, 5, 'stopped')
    assert _events(H1) == []
    assert (tv_downloads.status(H1) or {}).get('error') == tv_downloads.ALREADY_IMPORTED


def test_a_deleted_manual_import_is_not_copied_back(dl, time_show, monkeypatch):
    sid, eps = time_show
    eid = eps[3]
    _off(sid, [eid])
    _grab_row(H1, sid, [eid], origin='manual')
    src = _src(dl, NAME, NAME + '.mkv')
    grab(dl, H1, NAME, [NAME + '.mkv'])
    source = src.read_bytes()
    now = time.time()
    assert dl.tick(now)['imported'] == 1
    dest = _dest(dl, NAME + '.mkv')
    assert _linked(eid)[0]['state'] == 'linked'
    dest.unlink()
    tv_watch._link_shows(tv_watch._shows(), now + 30)
    gone = rows("SELECT episode_ids, detail FROM history WHERE event = 'deleted' AND series_id = ?", sid)
    assert len(gone) == 1 and json.loads(gone[0]['episode_ids']) == [eid]
    with tv_store.write() as conn:
        conn.execute("UPDATE grabs SET state = 'downloading', finished_at = NULL WHERE hash = ?", (H1,))
    _forbid(monkeypatch)
    policy = _policy(sid)
    for k in range(3):
        assert dl.tick(now + 60 * (k + 1))['imported'] == 0
    assert not dest.exists() and src.read_bytes() == source
    assert len(rows("SELECT 1 FROM history WHERE event = 'deleted' AND series_id = ?", sid)) == 1
    assert _policy(sid) == policy


def test_a_single_episode_request_does_not_import_pack_siblings(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [eps[3], eps[4]], show=False)
    _grab_row(H1, sid, [eps[3]], origin='manual')
    grab(dl, H1, NAME, [NAME + '.mkv', E4 + '.mkv'])
    e4src = _src(dl, NAME, E4 + '.mkv')
    e4bytes = e4src.read_bytes()
    policy = _policy(sid)
    _forbid(monkeypatch)
    assert dl.tick()['imported'] == 1
    assert _dest(dl, NAME + '.mkv').is_file()
    assert not _dest(dl, E4 + '.mkv').exists()
    assert _linked(eps[3])[0]['state'] == 'linked'
    assert _linked(eps[4]) == []
    assert e4src.read_bytes() == e4bytes
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'imported'
    assert _policy(sid) == policy


def test_an_automatic_single_episode_does_not_import_pack_siblings(dl, time_show, monkeypatch):
    sid, eps = time_show
    _grab_row(H1, sid, [eps[3]], origin='auto')
    grab(dl, H1, NAME, [NAME + '.mkv', E4 + '.mkv'])
    _forbid(monkeypatch)
    assert dl.tick()['imported'] == 1
    assert _dest(dl, NAME + '.mkv').is_file()
    assert not _dest(dl, E4 + '.mkv').exists()
    assert _linked(eps[4]) == []


def test_a_combined_file_that_includes_an_unwanted_episode_is_not_imported(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [eps[3], eps[4]])
    _grab_row(H1, sid, [eps[3]], origin='manual')
    grab(dl, H1, BOTH, [BOTH + '.mkv'])
    _forbid(monkeypatch)
    assert dl.tick()['imported'] == 0
    assert not _dest(dl, BOTH + '.mkv').exists()
    assert _linked(eps[3]) == [] and _linked(eps[4]) == []
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'downloading'
    assert (tv_downloads.status(H1) or {}).get('error') == tv_downloads.NO_EPISODES


# ---------------------------------------------------------------- file already landed by recovery

def test_already_linked_manual_file_is_reconciled_without_another_copy(dl, time_show, monkeypatch):
    """The episode file is already in the library and linked. The grab is still downloading and the
    ledger is still waiting / episode not monitored, which is the stuck request after a copy done
    outside the importer. The watcher closes the grab from the link. It does not copy, overwrite, or
    write an import record that claims the mover placed the file."""
    sid, eps = time_show
    eid = eps[3]
    _off(sid, [eid])
    _grab_row(H1, sid, [eid], origin='manual')
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'downloading'
    src = _src(dl, NAME, NAME + '.mkv')
    grab(dl, H1, NAME, [NAME + '.mkv', E4 + '.mkv'])
    source = src.read_bytes()
    dest = _dest(dl, NAME + '.mkv')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(LANDED)
    assert tv_watch.link_path(str(dest)) is True
    link = _linked(eid)
    assert len(link) == 1 and link[0]['state'] == 'linked' and link[0]['size'] == len(LANDED)
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'downloading'
    assert _events(H1) == []
    tv_downloads._save(H1, state='waiting', error=tv_downloads.UNMONITORED, at=time.time() - 50, name=NAME)
    ino = dest.stat().st_ino
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    assert dl.tick(now)['imported'] == 0
    assert dest.read_bytes() == LANDED and dest.stat().st_ino == ino
    assert not dest.is_symlink() and src.read_bytes() == source and src.is_file()
    assert not _dest(dl, E4 + '.mkv').exists()
    assert _linked(eps[4]) == []
    row = rows('SELECT state, results, origin FROM grabs WHERE hash = ?', H1)[0]
    assert row['origin'] == 'manual' and row['state'] == 'imported'
    results = json.loads(row['results'])
    assert results['via'] == 'reconciled' and results['files'] == 0 and results['episode_ids'] == [eid]
    ev = _events(H1)
    assert len(ev) == 1
    detail = json.loads(ev[0]['detail'])
    assert detail['via'] == 'reconciled' and detail['files'] == 0 and detail['origin'] == 'manual'
    st = tv_downloads.status(H1)
    assert st['state'] == 'refused' and st['error'] == tv_downloads.ALREADY_IMPORTED
    assert 'files' not in st
    assert _policy(sid) == policy and rows('SELECT monitored FROM episodes WHERE id = ?', eid)[0]['monitored'] == 0
    for k in range(2):
        assert dl.tick(now + 60 * (k + 1)) == {'checked': 0, 'imported': 0}
    assert dest.read_bytes() == LANDED and len(_events(H1)) == 1
    assert rows('SELECT state, results FROM grabs WHERE hash = ?', H1)[0]['results'] == row['results']
    assert _policy(sid) == policy
    _no_client_write(dl)


def _stuck(dl, sid, eid):
    """Open manual grab, unmonitored episode, ledger waiting. Returns (source path, source bytes)."""
    _off(sid, [eid])
    _grab_row(H1, sid, [eid], origin='manual')
    src = _src(dl, NAME, NAME + '.mkv')
    grab(dl, H1, NAME, [NAME + '.mkv', E4 + '.mkv'])
    tv_downloads._save(H1, state='waiting', error=tv_downloads.UNMONITORED, at=time.time() - 50, name=NAME)
    return src, src.read_bytes()


def _land_linked(dl, eid):
    dest = _dest(dl, NAME + '.mkv')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(LANDED)
    assert tv_watch.link_path(str(dest)) is True
    link = _linked(eid)
    assert len(link) == 1 and link[0]['state'] == 'linked' and link[0]['size'] == len(LANDED)
    return dest


def _still_open(dl, sid, src, source, policy):
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'downloading'
    assert _events(H1) == []
    st = tv_downloads.status(H1) or {}
    assert st.get('state') != 'imported'
    assert not (st.get('state') == 'refused' and st.get('error') == tv_downloads.ALREADY_IMPORTED)
    assert 'files' not in st
    assert src.read_bytes() == source and not _dest(dl, E4 + '.mkv').exists()
    assert _policy(sid) == policy
    return st


def _imported_once(dl, eid, src, source, policy, sid):
    dest = _dest(dl, NAME + '.mkv')
    assert dest.is_file() and not dest.is_symlink() and dest.read_bytes() == source
    assert src.read_bytes() == source and not _dest(dl, E4 + '.mkv').exists()
    assert _linked(eid)[0]['state'] == 'linked' and _linked(eid)[0]['size'] == len(source)
    row = rows('SELECT state, results FROM grabs WHERE hash = ?', H1)[0]
    assert row['state'] == 'imported'
    results = json.loads(row['results'])
    assert results['via'] == 'import' and results['files'] == 1 and results['episode_ids'] == [eid]
    ev = _events(H1)
    assert len(ev) == 1 and json.loads(ev[0]['detail'])['via'] == 'import'
    assert json.loads(ev[0]['detail'])['files'] == 1
    st = tv_downloads.status(H1)
    assert st['state'] == 'imported' and st['files'][0]['how'] in ('copied', 'linked')
    assert _policy(sid) == policy
    assert dl.tick() == {'checked': 0, 'imported': 0}
    assert len(_events(H1)) == 1 and dest.read_bytes() == source


def test_a_missing_linked_file_is_not_delivery_and_imports_once(dl, time_show, monkeypatch):
    sid, eps = time_show
    eid = eps[3]
    src, source = _stuck(dl, sid, eid)
    dest = _land_linked(dl, eid)
    dest.unlink()
    assert _linked(eid)[0]['state'] == 'linked'
    policy = _policy(sid)
    _forbid(monkeypatch)
    assert tv_downloads.reconcile() == 0
    assert _events(H1) == []
    assert dl.tick()['imported'] == 1
    _imported_once(dl, eid, src, source, policy, sid)


def test_a_linked_row_for_a_file_that_was_never_created_is_not_delivery(dl, time_show, monkeypatch):
    sid, eps = time_show
    eid = eps[3]
    src, source = _stuck(dl, sid, eid)
    dest = _dest(dl, NAME + '.mkv')
    rel = f"Time (2021)/Season 03/{NAME}.mkv"
    with tv_store.write() as conn:
        conn.execute(
            'INSERT INTO episode_files (series_id, path_id, rel_path, size, mtime, dev, ino, origin, added_at, state)'
            " VALUES (?, ?, ?, ?, 0, 0, 0, 'scan', 1, 'linked')",
            (sid, PID, rel, len(LANDED)))
        fid = conn.execute('SELECT id FROM episode_files WHERE rel_path = ?', (rel,)).fetchone()['id']
        conn.execute('INSERT INTO episode_file_links (file_id, episode_id) VALUES (?, ?)', (fid, eid))
    policy = _policy(sid)
    _forbid(monkeypatch)
    assert not dest.exists()
    assert tv_downloads.reconcile() == 0 and _events(H1) == []
    assert dl.tick()['imported'] == 1
    _imported_once(dl, eid, src, source, policy, sid)


@pytest.mark.parametrize('payload', [b'trunc', b'', b'x' * 20])
def test_a_truncated_or_empty_linked_file_stays_open_until_the_name_is_free(dl, time_show, monkeypatch, payload):
    """Stored size stays the landed size. A shorter file, including one still over the video-size floor,
    is not delivery and is not rewritten. The request stays waiting until the name is free."""
    sid, eps = time_show
    eid = eps[3]
    src, source = _stuck(dl, sid, eid)
    dest = _land_linked(dl, eid)
    dest.write_bytes(payload)
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    assert tv_downloads.reconcile(now) == 0
    for k in range(2):
        assert dl.tick(now + 60 * k)['imported'] == 0
    assert dest.read_bytes() == payload and not dest.is_symlink()
    assert _linked(eid)[0]['state'] == 'linked' and _linked(eid)[0]['size'] == len(LANDED)
    st = _still_open(dl, sid, src, source, policy)
    assert st.get('state') == 'waiting' and st.get('error') == tv_downloads.FILE_NOT_USABLE
    dest.unlink()
    assert dl.tick(now + 180)['imported'] == 1
    _imported_once(dl, eid, src, source, policy, sid)


def test_an_unreachable_show_directory_is_not_a_reconciled_delivery(dl, time_show, monkeypatch):
    sid, eps = time_show
    eid = eps[3]
    src, source = _stuck(dl, sid, eid)
    dest = _land_linked(dl, eid)
    show = dest.parents[1]
    aside = show.with_name(show.name + '-aside')
    show.rename(aside)
    policy = _policy(sid)
    _forbid(monkeypatch)
    assert tv_downloads.reconcile() == 0 and _events(H1) == []
    assert dl.tick()['imported'] == 1
    assert aside.is_dir() and (aside / 'Season 03' / (NAME + '.mkv')).read_bytes() == LANDED
    _imported_once(dl, eid, src, source, policy, sid)


def test_an_unreachable_library_root_waits_and_does_not_grow_a_tree(dl, time_show, monkeypatch):
    sid, eps = time_show
    eid = eps[3]
    src, source = _stuck(dl, sid, eid)
    dest = _land_linked(dl, eid)
    ino = dest.stat().st_ino
    root = dl.lib
    away = root.with_name(root.name + '-unmounted')
    root.rename(away)
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    try:
        assert tv_downloads.reconcile(now) == 0
        for k in range(2):
            assert dl.tick(now + 60 * k)['imported'] == 0
        assert not root.exists()
        kept = away / 'Time (2021)' / 'Season 03' / (NAME + '.mkv')
        assert kept.read_bytes() == LANDED and kept.stat().st_ino == ino
        st = _still_open(dl, sid, src, source, policy)
        assert st.get('error') == 'library folder not available'
    finally:
        if away.exists() and not root.exists():
            away.rename(root)
    assert dl.tick(now + 180)['imported'] == 0
    assert dest.read_bytes() == LANDED and dest.stat().st_ino == ino
    row = rows('SELECT state, results FROM grabs WHERE hash = ?', H1)[0]
    assert row['state'] == 'imported' and json.loads(row['results'])['via'] == 'reconciled'
    assert json.loads(row['results'])['files'] == 0
    assert len(_events(H1)) == 1 and 'files' not in (tv_downloads.status(H1) or {})
    assert _policy(sid) == policy


def test_a_destination_symlink_is_not_delivery_and_is_left_in_place(dl, time_show, monkeypatch):
    sid, eps = time_show
    eid = eps[3]
    src, source = _stuck(dl, sid, eid)
    outside = dl.root / 'outside-target.mkv'
    outside.write_bytes(LANDED)
    dest = _dest(dl, NAME + '.mkv')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.symlink_to(outside)
    assert tv_watch.link_path(str(dest)) is False
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    for k in range(2):
        assert dl.tick(now + 60 * k)['imported'] == 0
    assert dest.is_symlink() and os.readlink(dest) == str(outside)
    assert outside.read_bytes() == LANDED and src.read_bytes() == source
    assert _linked(eid) == []
    st = _still_open(dl, sid, src, source, policy)
    assert st.get('state') == 'waiting' and st.get('error') == tv_downloads.FILE_NOT_USABLE
    dest.unlink()
    assert dl.tick(now + 180)['imported'] == 1
    assert not dest.is_symlink() and outside.read_bytes() == LANDED
    _imported_once(dl, eid, src, source, policy, sid)


# ---------------------------------------------------------------- a requested sibling is still missing

def _fresh(h):
    """A new read of tv.db. The qBit stand-in stays in this process; the file is what a restart sees."""
    import sqlite3
    conn = sqlite3.connect(f'file:{tv_store.db_path()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        grab_row = conn.execute(
            'SELECT state, origin, episode_ids, results, finished_at, error FROM grabs WHERE hash = ?', (h,)).fetchone()
        meta = conn.execute('SELECT value FROM meta WHERE key = ?', (f'tvdl:{h}',)).fetchone()
    finally:
        conn.close()
    return (dict(grab_row) if grab_row is not None else None,
            json.loads(meta['value']) if meta is not None else None)


def _pack_request(dl, sid, eps, monitored, sibling=True):
    e3, e4 = eps[3], eps[4]
    if not monitored:
        _off(sid, [e3, e4])
    _grab_row(H1, sid, [e3, e4], title=PACK, origin='manual')
    names = [NAME + '.mkv', E4 + '.mkv'] + ([E1 + '.mkv'] if sibling else [])
    folder = grab(dl, H1, PACK, names)
    sources = {name: (folder / name).read_bytes() for name in names}
    return e3, e4, folder, sources


def _sources_unchanged(folder, sources):
    for name, data in sources.items():
        path = folder / name
        assert path.is_file() and not path.is_symlink() and path.read_bytes() == data


@pytest.mark.parametrize('monitored', [True, False])
def test_a_two_episode_request_with_both_names_free_imports_once(dl, time_show, monkeypatch, monitored):
    sid, eps = time_show
    e3, e4, folder, sources = _pack_request(dl, sid, eps, monitored)
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    assert dl.tick(now) == {'checked': 1, 'imported': 1}
    for name, eid in ((NAME + '.mkv', e3), (E4 + '.mkv', e4)):
        dest = _dest(dl, name)
        assert dest.is_file() and not dest.is_symlink() and dest.read_bytes() == sources[name]
        assert _linked(eid)[0]['state'] == 'linked' and _linked(eid)[0]['size'] == len(sources[name])
    assert not _dest(dl, E1 + '.mkv').exists()
    _sources_unchanged(folder, sources)
    row = rows('SELECT state, origin, results FROM grabs WHERE hash = ?', H1)[0]
    assert row['state'] == 'imported' and row['origin'] == 'manual'
    assert json.loads(row['results']) == {'episode_ids': [e3, e4], 'files': 2, 'via': 'import'}
    ev = _events(H1)
    assert len(ev) == 1
    detail = json.loads(ev[0]['detail'])
    assert detail['state'] == 'imported' and detail['files'] == 2 and detail['via'] == 'import'
    assert detail['origin'] == 'manual'
    assert json.loads(ev[0]['episode_ids']) == [e3, e4]
    assert dl.tick(now + 60) == {'checked': 0, 'imported': 0}
    assert tv_downloads.retry(H1) is False and len(_events(H1)) == 1
    assert _policy(sid) == policy
    _no_client_write(dl)


@pytest.mark.parametrize('monitored', [True, False])
def test_a_mixed_request_stays_recoverable_until_every_requested_file_is_placed(dl, time_show, monkeypatch, monitored):
    """E03's library name is a symlink and E04's is free. E04 may copy. The request stays open, with no
    imported history, until E03's name is free. The next pass and Retry then place only E03."""
    sid, eps = time_show
    e3, e4, folder, sources = _pack_request(dl, sid, eps, monitored)
    outside = dl.root / 'outside-target.mkv'
    outside.write_bytes(OUTSIDE)
    e3dest = _dest(dl, NAME + '.mkv')
    e3dest.parent.mkdir(parents=True, exist_ok=True)
    e3dest.symlink_to(outside)
    policy = _policy(sid)
    _forbid(monkeypatch)
    now = time.time()
    assert dl.tick(now) == {'checked': 1, 'imported': 0}
    e4dest = _dest(dl, E4 + '.mkv')
    e4_ino = e4dest.stat().st_ino
    assert e4dest.is_file() and not e4dest.is_symlink() and e4dest.read_bytes() == sources[E4 + '.mkv']
    assert _linked(e4)[0]['state'] == 'linked' and _linked(e4)[0]['size'] == len(sources[E4 + '.mkv'])
    assert _linked(e3) == [] and not _dest(dl, E1 + '.mkv').exists()
    grab_row, ledger = _fresh(H1)
    assert grab_row['state'] == 'downloading' and grab_row['origin'] == 'manual'
    assert grab_row['finished_at'] is None and grab_row['results'] is None
    assert json.loads(grab_row['episode_ids']) == [e3, e4]
    assert ledger['state'] == 'waiting' and ledger['error'] == tv_downloads.FILE_NOT_USABLE
    assert e3 not in (ledger.get('episode_ids') or []) and 'files' not in ledger
    assert _events(H1) == []
    assert tv_downloads.reconcile(now + 30) == 0
    for k in (1, 2):
        assert dl.tick(now + 60 * k) == {'checked': 1, 'imported': 0}
    assert e4dest.stat().st_ino == e4_ino and e4dest.read_bytes() == sources[E4 + '.mkv']
    assert e3dest.is_symlink() and os.readlink(e3dest) == str(outside)
    assert outside.read_bytes() == OUTSIDE and _sources_unchanged(folder, sources) is None
    assert tv_downloads.retry(H1) is True
    assert H1 not in {row['hash'] for row in tv_downloads.failures(now + 150)}
    assert dl.tick(now + 180) == {'checked': 1, 'imported': 0}
    assert _events(H1) == [] and rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'downloading'
    e3dest.unlink()
    assert not e3dest.exists()
    grab_row, ledger = _fresh(H1)
    assert grab_row['state'] == 'downloading' and grab_row['origin'] == 'manual'
    assert ledger['state'] == 'waiting'
    assert tv_downloads.reconcile(now + 200) == 0
    assert tv_downloads.retry(H1) is True
    assert dl.tick(now + 240) == {'checked': 1, 'imported': 1}
    assert e3dest.is_file() and not e3dest.is_symlink() and e3dest.read_bytes() == sources[NAME + '.mkv']
    assert e4dest.stat().st_ino == e4_ino and e4dest.read_bytes() == sources[E4 + '.mkv']
    assert outside.read_bytes() == OUTSIDE and not _dest(dl, E1 + '.mkv').exists()
    _sources_unchanged(folder, sources)
    assert _linked(e3)[0]['state'] == 'linked' and _linked(e3)[0]['size'] == len(sources[NAME + '.mkv'])
    assert _linked(e4)[0]['size'] == len(sources[E4 + '.mkv'])
    row = rows('SELECT state, origin, results FROM grabs WHERE hash = ?', H1)[0]
    assert row['state'] == 'imported' and row['origin'] == 'manual'
    assert json.loads(row['results']) == {'episode_ids': [e3, e4], 'files': 2, 'via': 'import'}
    ev = _events(H1)
    assert len(ev) == 1
    detail = json.loads(ev[0]['detail'])
    assert detail == {'hash': H1, 'title': PACK, 'files': 2, 'via': 'import', 'origin': 'manual', 'state': 'imported'}
    assert json.loads(ev[0]['episode_ids']) == [e3, e4]
    final = tv_downloads.status(H1)
    assert final['state'] == 'imported' and sorted(final['episode_ids']) == [e3, e4]
    by_name = {os.path.basename(f['path']): f for f in final['files']}
    assert by_name[NAME + '.mkv']['how'] == 'copied' and by_name[NAME + '.mkv']['linked'] is True
    assert by_name[E4 + '.mkv']['how'] == 'exists' and by_name[E4 + '.mkv']['linked'] is True
    assert set(by_name) == {NAME + '.mkv', E4 + '.mkv'}
    assert dl.tick(now + 300) == {'checked': 0, 'imported': 0}
    assert tv_downloads.retry(H1) is False and len(_events(H1)) == 1
    assert e4dest.stat().st_ino == e4_ino and _policy(sid) == policy
    _no_client_write(dl)


@pytest.mark.parametrize('monitored', [True, False])
@pytest.mark.parametrize('stopped', ['failed', 'skipped'])
def test_cancelling_a_mixed_request_does_not_place_the_remaining_file(dl, time_show, monkeypatch, monitored, stopped):
    """The stored cancel is the grab leaving downloading. Retry and the next pass do not finish it."""
    sid, eps = time_show
    e3, e4, folder, sources = _pack_request(dl, sid, eps, monitored)
    outside = dl.root / 'outside-target.mkv'
    outside.write_bytes(OUTSIDE)
    e3dest = _dest(dl, NAME + '.mkv')
    e3dest.parent.mkdir(parents=True, exist_ok=True)
    e3dest.symlink_to(outside)
    _forbid(monkeypatch)
    now = time.time()
    assert dl.tick(now)['imported'] == 0
    e4dest = _dest(dl, E4 + '.mkv')
    e4_ino = e4dest.stat().st_ino
    assert rows('SELECT state FROM grabs WHERE hash = ?', H1)[0]['state'] == 'downloading'
    assert _events(H1) == []
    with tv_store.write() as conn:
        conn.execute('UPDATE grabs SET state = ?, finished_at = ?, error = ? WHERE hash = ?',
                     (stopped, now, 'stopped', H1))
    e3dest.unlink()
    grab_row, ledger = _fresh(H1)
    assert grab_row['state'] == stopped and ledger['state'] == 'waiting'
    assert tv_downloads.reconcile(now + 30) == 0
    assert dl.tick(now + 60)['imported'] == 0
    assert not e3dest.exists() and e4dest.stat().st_ino == e4_ino
    assert _events(H1) == []
    assert rows('SELECT state, finished_at, error FROM grabs WHERE hash = ?', H1)[0]['state'] == stopped
    assert tv_downloads.retry(H1) is True
    assert dl.tick(now + 120)['imported'] == 0
    assert not e3dest.exists() and e4dest.read_bytes() == sources[E4 + '.mkv'] and e4dest.stat().st_ino == e4_ino
    assert outside.read_bytes() == OUTSIDE and _sources_unchanged(folder, sources) is None
    assert _linked(e3) == [] and _events(H1) == []
    assert rows('SELECT state, error FROM grabs WHERE hash = ?', H1)[0]['state'] == stopped
    _no_client_write(dl)


def test_a_historical_partial_is_not_resumed(dl, time_show, monkeypatch):
    """A partial row that already existed is not this request's unfinished pass. It stays partial."""
    sid, eps = time_show
    e3, e4 = eps[3], eps[4]
    _grab_row(H1, sid, [e3, e4], title=PACK, origin='manual')
    with tv_store.write() as conn:
        conn.execute("UPDATE grabs SET state = 'partial', finished_at = 5, results = ?, error = NULL WHERE hash = ?",
                     (json.dumps({'episode_ids': [e4], 'files': 1, 'via': 'import'}), H1))
    tv_downloads._save(H1, state='imported', episode_ids=[e4], files=[{'path': 'old', 'how': 'copied', 'linked': True}],
                       at=5, name=PACK)
    folder = grab(dl, H1, PACK, [NAME + '.mkv', E4 + '.mkv'])
    _forbid(monkeypatch)
    now = time.time()
    assert tv_downloads.reconcile(now) == 0
    assert dl.tick(now)['imported'] == 0
    assert tv_downloads.retry(H1) is False
    assert dl.tick(now + 60)['imported'] == 0
    assert not _dest(dl, NAME + '.mkv').exists() and not _dest(dl, E4 + '.mkv').exists()
    row = rows('SELECT state, finished_at, results FROM grabs WHERE hash = ?', H1)[0]
    assert row['state'] == 'partial' and row['finished_at'] == 5
    assert json.loads(row['results'])['episode_ids'] == [e4]
    assert _events(H1) == []
    assert (folder / (NAME + '.mkv')).read_bytes() == b'episode-bytes' * 10
    _no_client_write(dl)


def test_deleting_an_unmonitored_mixed_request_does_not_import_the_rest(dl, time_show, monkeypatch):
    sid, eps = time_show
    e3, e4, folder, sources = _pack_request(dl, sid, eps, monitored=False, sibling=False)
    outside = dl.root / 'outside-target.mkv'
    outside.write_bytes(OUTSIDE)
    e3dest = _dest(dl, NAME + '.mkv')
    e3dest.parent.mkdir(parents=True, exist_ok=True)
    e3dest.symlink_to(outside)
    _forbid(monkeypatch)
    now = time.time()
    assert dl.tick(now)['imported'] == 0
    e4_ino = _dest(dl, E4 + '.mkv').stat().st_ino
    with tv_store.write() as conn:
        conn.execute('DELETE FROM grabs WHERE hash = ?', (H1,))
    e3dest.unlink()
    assert tv_downloads.reconcile(now + 30) == 0
    assert dl.tick(now + 60)['imported'] == 0
    assert not e3dest.exists() and _dest(dl, E4 + '.mkv').stat().st_ino == e4_ino
    assert rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 0
    assert _events(H1) == [] and _linked(e3) == []
    assert (tv_downloads.status(H1) or {}).get('state') != 'imported'
    assert outside.read_bytes() == OUTSIDE and _sources_unchanged(folder, sources) is None
    _no_client_write(dl)
