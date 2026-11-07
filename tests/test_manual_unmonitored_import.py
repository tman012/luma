"""An explicit manual grab imports when the episode or the show is unmonitored.

Monitoring still refuses automatic and upgrade grabs. The authorization is the grabs row Luma wrote
(origin manual, still open, with episode ids), which survives a process restart. A qBittorrent category
or a release name is not a request. Imported, partial, failed, skipped and manual_needed rows stay
closed. A grab names the episodes it may place, so a pack does not import siblings that were not requested.

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

E4 = 'Time.S03E04.1080p.WEBRip.X264-DEADPOOL'
BOTH = 'Time.S03E03E04.1080p.WEBRip.X264-DEADPOOL'
LANDED = b'landed-by-recovery-not-the-importer'


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

def test_category_without_a_grab_row_is_not_a_manual_request(dl, time_show, monkeypatch):
    """The category watch can still file a torrent that has no grab row (v158). That import is not a
    manual request: no grabs row is created, monitoring does not change, and the history has no manual origin."""
    sid, eps = time_show
    _off(sid, [eps[3]], show=False)
    grab(dl, H1, NAME, [NAME + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    assert dl.tick()['imported'] == 1
    assert _dest(dl, NAME + '.mkv').is_file() and _linked(eps[3])[0]['state'] == 'linked'
    assert rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 0
    ev = _events(H1)
    assert len(ev) == 1 and 'origin' not in json.loads(ev[0]['detail'])
    assert _policy(sid) == policy
    assert dl.tick()['imported'] == 0 and rows('SELECT COUNT(*) AS n FROM grabs')[0]['n'] == 0


def test_a_name_that_says_manual_does_not_authorize_or_monitor(dl, time_show, monkeypatch):
    sid, eps = time_show
    _off(sid, [eps[3]])
    title = 'Time.S03E03.1080p.WEBRip.manual-DEADPOOL'
    grab(dl, H1, title, [title + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    dl.tick()
    assert rows("SELECT COUNT(*) AS n FROM grabs WHERE origin = 'manual'")[0]['n'] == 0
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
    """A closed row matches by hash only. Another torrent of the same name is not that request and
    does not reopen it. With no open row of its own, it is the category watch, not a new manual grab."""
    sid, eps = time_show
    _off(sid, [eps[3]])
    _grab_row(H1, sid, [eps[3]], origin='manual')
    with tv_store.write() as conn:
        conn.execute("UPDATE grabs SET state = 'imported', finished_at = 5 WHERE hash = ?", (H1,))
    grab(dl, H2, NAME, [NAME + '.mkv'])
    policy = _policy(sid)
    _forbid(monkeypatch)
    assert tv_downloads._no_import(H2, NAME) is None
    assert tv_downloads._no_import(H1, NAME) == tv_downloads.ALREADY_IMPORTED
    dl.tick()
    assert rows('SELECT state, finished_at FROM grabs WHERE hash = ?', H1)[0]['finished_at'] == 5
    assert rows("SELECT COUNT(*) AS n FROM grabs WHERE origin = 'manual' AND state = 'downloading'")[0]['n'] == 0
    ev = _events(H2)
    assert not ev or json.loads(ev[0]['detail']).get('origin') != 'manual'
    assert _policy(sid) == policy


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
