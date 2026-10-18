"""Reviewer's race probes made real tests (R1 on 7028c30: the watcher's unlocked walk vs a
'Wrong show?'; and the owner acting while the v161a boot re-run matches unlocked)."""
import threading

from app import tv_match, tv_scan, tv_store, tv_watch
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, _old_rows, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def test_r1_watch_walk_vs_wrong_show(scanned, monkeypatch):
    """tv_watch walks a show's folder unlocked; a 'Wrong show?' lands before the locked store."""
    sid = rows('SELECT id FROM series WHERE tmdb_id = 95396')[0]['id']
    shows = [s for s in tv_watch._shows() if int(s['id']) == sid]
    assert shows, tv_watch._shows()
    real = tv_scan.list_files
    done = {}
    def walk(folder, root):
        out = real(folder, root)
        if not done:
            done['go'] = 1
            t = threading.Thread(target=lambda: done.setdefault('r', tv_scan.rematch(sid, 1001, now=7000.0)))
            t.start(); t.join()
        return out
    monkeypatch.setattr(tv_scan, 'list_files', walk)
    tv_watch._link_shows(shows, 8000.0)
    assert done['r']['ok'], done
    nid = rows("SELECT id FROM series WHERE folder = 'Severance (2022)'")[0]['id']
    owners = rows("SELECT DISTINCT series_id FROM episode_files WHERE rel_path LIKE 'Severance (2022)/%'")
    assert {o['series_id'] for o in owners} <= {nid}, 'files of the folder re-attributed to the detached show'


def test_owner_acts_during_boot_rerun_match(lib, monkeypatch, fake_tmdb):
    """While the boot re-run is matching (unlocked), the owner Matches a review row by hand and
    rematches an imported show. The re-run must not overwrite either."""
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', True)
    tv_scan.scan_all(now=400.0)
    with tv_store.write() as conn:
        conn.execute('DELETE FROM unmatched')
        conn.execute("DELETE FROM series WHERE folder LIKE 'Taskmaster%' OR folder LIKE 'Dark%'")
    _old_rows(lib)
    bluey = rows("SELECT id FROM unmatched WHERE folder = 'Bluey (2018)'")[0]['id']
    imported = [dict(r) for r in rows('SELECT id, tmdb_id, folder FROM series WHERE folder IS NOT NULL')]
    victim = imported[0]
    real = tv_match.match
    done = {}

    def match(folder, hints=None, *a, **k):
        if not done:
            done['go'] = 1
            def owner():
                done['m'] = tv_scan.match_unmatched(bluey, 1002, now=1500.0)        # owner picks Mystery Show
                done['r'] = tv_scan.rematch(victim['id'], 63770, now=1500.0)        # and a "Wrong show?"
            t = threading.Thread(target=owner); t.start(); t.join()
        return real(folder, hints, *a, **k)
    monkeypatch.setattr(tv_match, 'match', match)
    out = tv_scan.auto_import_pending(now=2000.0)
    assert done['m']['ok'], done
    got = {r['folder']: r['tmdb_id'] for r in rows('SELECT folder, tmdb_id FROM series WHERE folder IS NOT NULL')}
    assert got['Bluey (2018)'] == 1002, got                     # the owner's Match stands
    if done['r']['ok']:
        assert got[victim['folder']] == 63770, got              # the owner's Wrong show? stands
    assert not rows("SELECT 1 FROM unmatched WHERE folder = 'Bluey (2018)'")
