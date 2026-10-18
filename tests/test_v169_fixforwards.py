"""v169 fix-forwards from Reviewer (58c6fab, a21896b, 3e0df8a), each with tests:
1) trust_env=False on every indexer_api session (ProwlarrClient search + download, TorznabClient);
2) M34: lost+found, @eaDir, #recycle and .Trash-* don't make a library path non-empty, and a dropped
   mount (os.path.ismount) counts as empty, so the mass detach is blocked;
3) a read-only Movies path is never the implicit fallback; the Downloads item says 'No writable
   destination' (status no_writable) at once instead of a silent NOT_WRITABLE wait;
4) ProwlarrClient.owns refuses '.'/'..' segments, raw or encoded (/prowlarr/../admin fails);
5) the import-time rule fingerprint (tv_scan._rule_id) never raises.
"""
import os
import re
import shutil
import pathlib

import pytest

from app import file_management as fm
from app import indexer_api, library_mover as lm, tv_match, tv_scan
from tests.test_file_management import store  # noqa: F401
from tests.test_library_mover import H1, TMDB, add_path, env, job, movie_file, torrent  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_m34 import _state, _visible
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

APP = pathlib.Path(__file__).resolve().parent.parent / 'app'
NOT_ROOT = pytest.mark.skipif(os.geteuid() == 0, reason='root ignores the read-only bit')


# ------------------------------------------------------------------ 1) trust_env everywhere

def test_every_indexer_session_ignores_the_environment():
    p = indexer_api.ProwlarrClient('http://10.0.0.5:9696', 'k')
    t = indexer_api.TorznabClient('https://t.example/api', 'k')
    assert p.session.trust_env is False
    assert p._download_session().trust_env is False
    assert t.session.trust_env is False


def test_no_indexer_session_is_made_without_trust_env_off():
    src = (APP / 'indexer_api.py').read_text()
    made = [m.start() for m in re.finditer(r'requests\.Session\(\)', src)]
    assert len(made) >= 3
    for at in made:
        assert 'trust_env = False' in src[at:at + 400], src[at - 80:at + 200]


def test_a_proxy_in_the_environment_is_not_used(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://10.9.9.9:3128')
    monkeypatch.setenv('HTTPS_PROXY', 'http://10.9.9.9:3128')
    p = indexer_api.ProwlarrClient('http://10.0.0.5:9696', 'k')
    settings = p.session.merge_environment_settings('http://10.0.0.5:9696/api/v1/search', {}, None, None, None)
    assert not settings.get('proxies')


# ------------------------------------------------------------------ 4) owns() and dot segments

@pytest.mark.parametrize('url', [
    'http://10.0.0.5:9696/prowlarr/../admin',
    'http://10.0.0.5:9696/prowlarr/%2e%2e/admin',
    'http://10.0.0.5:9696/prowlarr/%2E%2E/admin',
    'http://10.0.0.5:9696/prowlarr/%252e%252e/admin',
    'http://10.0.0.5:9696/prowlarr/..%2fadmin',
    'http://10.0.0.5:9696/prowlarr/x%5c..%5cadmin',
    'http://10.0.0.5:9696/prowlarr/./1/download',
    'http://10.0.0.5:9696/prowlarr/1/..',
    'http://10.0.0.5:9696/prowlarrx/1/download',
    'http://10.0.0.5:9697/prowlarr/1/download',
    'https://10.0.0.5:9696/prowlarr/1/download',
])
def test_owns_refuses_dot_segments_and_other_origins(url):
    assert indexer_api.ProwlarrClient('http://10.0.0.5:9696/prowlarr', 'k').owns(url) is False


def test_owns_refuses_dot_segments_with_no_url_base_too():
    c = indexer_api.ProwlarrClient('http://10.0.0.5:9696', 'k')
    assert c.owns('http://10.0.0.5:9696/../etc/passwd') is False
    assert c.owns('http://10.0.0.5:9696/api/v1/indexer/3/download?link=x') is True


def test_owns_still_takes_real_links():
    c = indexer_api.ProwlarrClient('http://10.0.0.5:9696/prowlarr', 'k')
    assert c.owns('http://10.0.0.5:9696/prowlarr/3/download?apikey=k&link=abc')
    assert c.owns('http://10.0.0.5:9696/prowlarr/3/download/Some..Release.torrent')


def test_a_dot_segment_link_is_refused_with_nothing_fetched(monkeypatch):
    c = indexer_api.ProwlarrClient('http://10.0.0.5:9696/prowlarr', 'k')
    monkeypatch.setattr(c, '_download_session', lambda: (_ for _ in ()).throw(AssertionError('fetched')))
    with pytest.raises(ValueError):
        c.download('http://10.0.0.5:9696/prowlarr/../api/v1/system/backup')


# ------------------------------------------------------------------ 5) _rule_id never raises

@pytest.mark.parametrize('boom', [OSError, TypeError, ValueError, RuntimeError, UnicodeDecodeError])
def test_rule_id_falls_back_to_the_rule_name_on_any_error(monkeypatch, boom):
    import inspect

    def bad(_):
        if boom is UnicodeDecodeError:
            raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'bad')
        raise boom('no source')
    monkeypatch.setattr(inspect, 'getsource', bad)
    assert tv_scan._rule_id() == tv_match.RULE_VERSION


def test_rule_id_normally_carries_the_fingerprint():
    assert re.fullmatch(re.escape(tv_match.RULE_VERSION) + r'\+[0-9a-f]{10}', tv_scan._rule_id())


# ------------------------------------------------------------------ 2) M34: system folders, dropped mounts

@pytest.mark.parametrize('junk', [['lost+found'], ['@eaDir'], ['#recycle'], ['.Trash-1000'],
                                  ['lost+found', '@eaDir', '#recycle', '.Trash-0']])
def test_a_mount_point_with_only_system_folders_counts_as_empty(lib, tmp_path, junk):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    before, seen = _state(), _visible()
    assert before[0]
    away = tmp_path / 'share-contents'
    shutil.move(str(lib), str(away))
    lib.mkdir()
    for j in junk:
        (lib / j).mkdir()
    out = tv_scan.scan_all(now=2000.0)
    assert out['missing'] == [PID]
    assert _state() == before and _visible() == seen          # no mass detach


def test_a_dropped_mount_with_only_junk_left_detaches_nothing(lib, tmp_path, monkeypatch):  # noqa: F811
    """v170 (Reviewer): a dropped mount counts as empty only when no show folders are left."""
    real_ismount = os.path.ismount
    mounted = {'on': True}
    root = os.path.realpath(lib)
    monkeypatch.setattr(os.path, 'ismount', lambda p: (mounted['on'] and os.path.realpath(p) == root) or real_ismount(p))
    tv_scan.scan_all(now=1000.0)
    assert tv_scan._mount_of(root) == root
    before, seen = _state(), _visible()
    away = tmp_path / 'share-contents'
    shutil.move(str(lib), str(away))
    lib.mkdir()
    for j in ('lost+found', '@eaDir', '.Trash-1000'):
        (lib / j).mkdir()
    mounted['on'] = False                                     # the NAS dropped
    out = tv_scan.scan_all(now=2000.0)
    assert out['missing'] == [PID]
    assert _state() == before and _visible() == seen          # no mass detach
    shutil.rmtree(lib)
    shutil.move(str(away), str(lib))
    mounted['on'] = True                                      # back
    out = tv_scan.scan_all(now=3000.0)
    assert out['missing'] == [] and _state()[0] == before[0]


def test_a_retired_mount_with_shows_present_scans_and_rerecords(lib, monkeypatch):  # noqa: F811
    """v170 (Reviewer, M34 wedge): the recorded mount is gone (ismount False) but the show folders
    are all there: the scan runs (it used to skip forever), picks up a new folder and re-records."""
    from app import tv_store
    real_ismount = os.path.ismount
    mounted = {'on': True}
    root = os.path.realpath(lib)
    monkeypatch.setattr(os.path, 'ismount', lambda p: (mounted['on'] and os.path.realpath(p) == root) or real_ismount(p))
    tv_scan.scan_all(now=1000.0)
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) == root
    before = _state()
    mounted['on'] = False                                     # mount retired; the folders now live on /
    (lib / 'Brand New Show (2026)').mkdir()
    out = tv_scan.scan_all(now=2000.0)
    assert out['missing'] == []
    folders = {r['folder'] for r in rows('SELECT folder FROM series WHERE path_id = ?', PID)} | \
        {r['folder'] for r in rows('SELECT folder FROM unmatched WHERE path_id = ?', PID)}
    assert 'Brand New Show (2026)' in folders                 # the scan ran and found it
    assert _state()[0] == before[0]                           # nothing detached
    assert tv_store.get_meta(tv_scan.MOUNT_META + PID) == tv_scan._mount_of(root) != root   # re-recorded
    out = tv_scan.scan_all(now=3000.0)
    assert out['missing'] == []                               # and stays unwedged


def test_editing_or_removing_a_path_clears_its_mount_record(lib):  # noqa: F811
    from app import tv_settings, tv_store  # noqa: F401 - registers the path hooks
    tv_scan.scan_all(now=1000.0)
    key = tv_scan.MOUNT_META + PID
    assert tv_store.get_meta(key)
    fm._path_repointed(PID)
    assert tv_store.get_meta(key) is None
    tv_store.set_meta(key, '/old-mount')
    fm._path_removed(PID)
    assert tv_store.get_meta(key) is None


def test_a_path_that_was_never_on_its_own_mount_scans_normally(lib, tmp_path):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    known = sorted({r['folder'] for r in rows('SELECT folder FROM series WHERE path_id = ? AND folder IS NOT NULL', PID)}
                   | {r['folder'] for r in rows('SELECT folder FROM unmatched WHERE path_id = ?', PID)})
    keep = known[: (len(known) + 1) // 2]                    # v171: at least half stay (PM coverage guard)
    away = tmp_path / 'away'
    away.mkdir()
    for f in os.listdir(lib):
        if f not in keep:
            shutil.move(str(lib / f), str(away / f))
    (lib / 'lost+found').mkdir()
    out = tv_scan.scan_all(now=2000.0)
    assert out['missing'] == []
    left = {r['folder'] for r in rows('SELECT folder FROM series WHERE path_id = ? AND folder IS NOT NULL', PID)}
    assert left and left <= set(keep)                         # real shows left: normal rules


# ------------------------------------------------------------------ 3) read-only Movies paths

@NOT_ROOT
def test_a_read_only_lone_movies_path_is_never_the_implicit_fallback(tmp_path):
    ro = tmp_path / 'ro'
    ro.mkdir()
    ro.chmod(0o555)
    try:
        data = {'paths': [{'id': 'p_1', 'name': 'RO', 'path': str(ro)}], 'rules': [], 'fallback_path_id': None}
        assert fm.effective_fallback(data, set()) is None
        assert fm.pick_destination(data, {}, taken=set()) is None
        assert fm.no_writable_destination(data, set()) is True
        ok = tmp_path / 'ok'
        ok.mkdir()
        data['paths'][0]['path'] = str(ok)
        assert fm.effective_fallback(data, set())['id'] == 'p_1'
        assert fm.no_writable_destination(data, set()) is False
        gone = {'paths': [{'id': 'p_1', 'name': 'X', 'path': str(tmp_path / 'not-mounted')}], 'rules': [],
                'fallback_path_id': None}
        assert fm.effective_fallback(gone, set())['id'] == 'p_1'      # not there: the mover waits, as before
    finally:
        ro.chmod(0o755)


@NOT_ROOT
def test_the_item_says_no_writable_destination_at_once_and_files_once_fixed(env):  # noqa: F811
    ro = env.media / 'Movies'
    add_path('Movies', ro)
    ro.chmod(0o555)
    try:
        movie_file(env, 'Heat.1995.1080p.BluRay.x264.mkv')
        lm.track(H1, TMDB, 'Heat.1995.1080p.BluRay.x264')
        torrent(env, H1, 'Heat.1995.1080p.BluRay.x264.mkv')
        env.tick()
        j = job(H1)
        assert j['state'] == 'waiting' and j['status'] == lm.NO_WRITABLE_DEST   # flagged on the first pass
        import app.main as main
        downloads = []
        main._mark_not_mapped(downloads, list(env.q.torrents.values()), {}, set())
        assert [d['status'] for d in downloads] == ['no_writable']
        rows_ = [{'hash': H1, 'status': 'downloading', 'state': 'downloading'}]
        main._mark_not_mapped(rows_, [], {}, set())
        assert rows_[0]['status'] == 'no_writable'
    finally:
        ro.chmod(0o755)
    assert env.tick()['filed'] == 1                           # writable again: filed, flag gone
    assert job(H1)['state'] == 'done'


@NOT_ROOT
def test_an_explicit_read_only_destination_is_flagged_not_a_silent_wait(env):  # noqa: F811
    ro = env.media / 'Full Rips'
    pid = add_path('Full Rips', ro)
    add_path('Movies', env.media / 'Movies')
    fm.update(lambda d: d.__setitem__('fallback_path_id', pid))
    ro.chmod(0o555)
    try:
        movie_file(env, 'Heat.1995.1080p.BluRay.x264.mkv')
        lm.track(H1, TMDB, 'Heat.1995.1080p.BluRay.x264')
        torrent(env, H1, 'Heat.1995.1080p.BluRay.x264.mkv')
        env.tick()
        assert job(H1)['status'] == lm.NO_WRITABLE_DEST
    finally:
        ro.chmod(0o755)


# ------------------------------------------------------------------ spacing never parks longer than min_interval_s

def test_a_last_sent_time_in_the_future_waits_at_most_the_spacing(monkeypatch, tmp_path):
    """A clock that stepped back (or a stale future timestamp) can't park a search or download for hours
    (found as a gate hang in v169); the 2 s spacing itself is unchanged."""
    import time as _t
    from app import tracker_guard
    now = _t.time()
    assert indexer_api._spacing_wait(now + 86400 * 90) == indexer_api.GUARD_LIMITS['min_interval_s'] == 2.0
    assert 1.9 < indexer_api._spacing_wait(now) <= 2.0
    assert indexer_api._spacing_wait(now - 10) < 0
    g = tracker_guard.TrackerGuard(tmp_path / 'g.json')
    g.last_sent['site:pixelhd'] = now + 86400 * 90
    slept = []
    monkeypatch.setattr(tracker_guard.time, 'sleep', slept.append)
    g.wait_spacing('site:pixelhd')
    assert slept and max(slept) <= 2.0
