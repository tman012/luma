"""Next round after 78d7f1e: PM cleanup ruling, reviewer follow-ups (3)(5)(6)(8),
QA-UI1 downloads status, no qBittorrent sign-in while the downloads folder is
missing, and QA-TV2/TV1 PUT rules."""
import os
import sqlite3
import stat

import pytest

from app import file_management as fm
from app import library_mover as lm
from app import qbit_owner, section_trackers as st, storage_cleanup, tv_settings, tv_store
from tests.test_file_management import HDR, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_library_mover import env, add_path, TMDB, H1  # noqa: F401
from tests.test_tv_foundations import RECS, sec  # noqa: F401

CLEANUP = '/api/storage/downloads/cleanup'
LAN = {'REMOTE_ADDR': '192.168.1.20'}


# ------------------------------------------------------------- manual cleanup (PM ruling)

@pytest.fixture
def full(test_app, monkeypatch):
    """A full (not minimal) build with cleanup stubbed out."""
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    calls = []
    monkeypatch.setattr(storage_cleanup, 'run_cleanup',
                        lambda target, **kw: (calls.append((target, kw)), {'success': True})[1])
    test_app.calls = calls
    return test_app


def _auth(mode):
    import app.main
    app.main.settings.update({'security': {'auth_required': mode}})


def test_the_implicit_lan_owner_must_sign_in_to_clean_up(full):
    # PM ruling (destructive file actions): LAN sign-in off doesn't count here.
    _client(full)                                  # the owner exists
    _auth('disabled_local')
    r = full.test_client().post(CLEANUP, json={'target_bytes': 7}, environ_base=LAN, headers=HDR)
    assert r.status_code == 401 and r.get_json() == {'error': 'Sign in first', 'sign_in': True}
    assert full.calls == []


def test_lan_without_implicit_sign_in_still_needs_the_owner(full):
    _client(full)
    _auth('enabled')                               # LAN must sign in too
    r = full.test_client().post(CLEANUP, json={}, environ_base=LAN, headers=HDR)
    assert r.status_code == 401 and full.calls == []


def test_others_never_delete(full, monkeypatch):
    import app.main
    _client(full)                                  # owner
    admin2 = _client(full, name='second')          # another admin
    viewer = _client(full, admin=False, name='viewer')   # plain / guest / Plex-approved viewer
    monkeypatch.setattr(app.main, '_SERVICE_API_KEY', 'svc-' + 'k' * 20)
    kiosk = dict(HDR, **{'X-API-Key': 'svc-' + 'k' * 20})   # kiosk and companion apps
    _auth('disabled_local')
    assert admin2.post(CLEANUP, json={}, environ_base=LAN, headers=HDR).status_code == 403
    assert viewer.post(CLEANUP, json={}, environ_base=LAN, headers=HDR).status_code == 403
    assert full.test_client().post(CLEANUP, json={}, environ_base=REMOTE, headers=kiosk).status_code == 401
    assert full.test_client().post(CLEANUP, json={}, environ_base=LAN, headers=kiosk).status_code == 401
    assert full.test_client().post(CLEANUP, json={}, environ_base=REMOTE, headers=HDR).status_code == 401
    assert full.calls == []


def test_minimal_builds_have_no_cleanup_route(test_app, monkeypatch):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    calls = []
    monkeypatch.setattr(storage_cleanup, 'run_cleanup', lambda target, **kw: calls.append(target) or {})
    _client(test_app)
    _auth('disabled_local')
    r = test_app.test_client().post(CLEANUP, json={}, environ_base=LAN, headers=HDR)
    assert r.status_code == 404 and calls == []      # minimal builds don't offer it, LAN or not


def test_the_cleanup_message_never_names_the_test_install():
    assert 'test install' not in storage_cleanup.QBIT_UNREADABLE
    assert storage_cleanup.QBIT_UNREADABLE == "Can't reach qBittorrent, so nothing was deleted"


# ------------------------------------------------------------- reviewer (3): app category reserved

def test_the_configured_app_category_is_reserved(monkeypatch):
    class S:
        def get(self, k, d=None):
            return {'qbittorrent.app_category': ' Luma-Prod '}.get(k, d)
    monkeypatch.setitem(qbit_owner._cfg, 'settings', S())
    monkeypatch.setenv(qbit_owner.TAG_ENV, 'luma-prod')
    assert qbit_owner.tag_name() == qbit_owner.DEFAULT_TAG
    monkeypatch.setenv(qbit_owner.TAG_ENV, 'luma-qa')
    assert qbit_owner.tag_name() == 'luma-qa'
    monkeypatch.setitem(qbit_owner._cfg, 'settings', None)
    monkeypatch.setenv(qbit_owner.TAG_ENV, 'luma-prod')
    assert qbit_owner.tag_name() == 'luma-prod'


# ------------------------------------------------------------- reviewer (5): tv naming specs (v185: Sonarr tokens)

@pytest.mark.parametrize('tpl', ['{Series Title:>10}', '{Series Title:02}', '{Series Title} ({Series Year:x})'])
def test_format_specs_only_on_number_fields(tpl):
    assert tv_settings._naming().check_format('series_folder', tpl) is not None


def test_year_is_formatted_as_a_number():
    data = {'naming': dict(tv_settings._naming().DEFAULTS, series_folder='{Series Title} [{Series Year}]')}
    assert tv_settings.series_folder('Show', '0999', data) == 'Show [0999]'
    assert tv_settings.series_folder('Show', 2008, data) == 'Show [2008]'
    assert tv_settings.series_folder('Show', 'abcd', data) == 'Show'


# ------------------------------------------------------------- reviewer (6): tv_store

@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / 'tv' / 'tv.db'
    monkeypatch.setitem(tv_store._cfg, 'path', str(p))
    monkeypatch.setattr(tv_store, '_ready', {})
    return p


def test_wal_and_shm_are_private_even_with_umask_0(db):
    old = os.umask(0)
    try:
        with tv_store.connect() as c:
            c.execute('SELECT 1').fetchone()
            tv_store.log_search(tracker_key='t:1', purpose='manual')
            for f in (db, db.with_name('tv.db-wal'), db.with_name('tv.db-shm')):
                assert f.exists(), f
                assert stat.S_IMODE(os.stat(f).st_mode) == 0o600, f
    finally:
        os.umask(old)


def test_a_lock_timeout_never_quarantines(db, monkeypatch):
    with tv_store.connect():
        pass
    monkeypatch.setattr(tv_store, '_ready', {})
    real = tv_store._open

    def locked(p):
        conn = real(p)
        conn.close()
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(tv_store, '_open', locked)
    with pytest.raises(sqlite3.OperationalError):
        with tv_store.connect():
            pass
    assert db.exists()
    assert not [f for f in db.parent.iterdir() if '.bad-' in f.name]


def test_is_corrupt_reads_the_sqlite_code():
    e = sqlite3.DatabaseError('x')
    e.sqlite_errorcode = 11
    assert tv_store.is_corrupt(e)
    e.sqlite_errorcode = 26
    assert tv_store.is_corrupt(e)
    busy = sqlite3.OperationalError('database is locked')
    busy.sqlite_errorcode = 5
    assert not tv_store.is_corrupt(busy)
    assert not tv_store.is_corrupt(sqlite3.OperationalError('unable to open database file'))
    assert tv_store.is_corrupt(tv_store.Corrupt('quick_check failed'))
    assert not tv_store.is_corrupt(OSError('disk'))


def test_a_corrupt_file_is_quarantined_and_replaced(db):
    db.parent.mkdir(parents=True)
    db.write_bytes(b'not a database at all' * 100)
    with tv_store.connect() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0] == tv_store.SCHEMA_VERSION
    (bad,) = [f for f in db.parent.iterdir() if '.bad-' in f.name]
    assert bad.read_bytes() == b'not a database at all' * 100


def test_a_quarantined_file_keeps_its_wal_and_shm(db, monkeypatch):
    db.parent.mkdir(parents=True)
    db.write_bytes(b'')
    real, seen = tv_store._open, []

    def corrupt_once(p):
        if not seen:
            seen.append(1)
            db.with_name('tv.db-wal').write_bytes(b'wal of the bad file')
            db.with_name('tv.db-shm').write_bytes(b'shm of the bad file')
            raise tv_store.Corrupt('quick_check failed')
        return real(p)
    monkeypatch.setattr(tv_store, '_open', corrupt_once)
    with tv_store.connect() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0] == tv_store.SCHEMA_VERSION
    (bad,) = [f for f in db.parent.iterdir() if '.bad-' in f.name and not f.name.endswith(('-wal', '-shm'))]
    assert bad.with_name(bad.name + '-wal').read_bytes() == b'wal of the bad file'
    assert bad.with_name(bad.name + '-shm').read_bytes() == b'shm of the bad file'


# ------------------------------------------------------------- reviewer (8): partials

def _dirfd(p):
    return os.open(str(p), os.O_RDONLY | os.O_DIRECTORY)


def test_a_partial_that_is_already_there_is_left_alone(tmp_path, monkeypatch):
    import secrets
    root = tmp_path / 'dl'
    root.mkdir()
    src = root / 'Movie.mkv'
    src.write_bytes(b'v' * 4096)
    lib = tmp_path / 'lib'
    lib.mkdir()
    foreign = lib / f'.Movie.mkv.aaaaaaaa{lm.PARTIAL_SUFFIX}'
    foreign.write_bytes(b'someone else')
    tokens = iter(['aaaaaaaa', 'bbbbbbbb'])
    monkeypatch.setattr(secrets, 'token_hex', lambda n: next(tokens))
    dfd = _dirfd(lib)
    try:
        assert lm.transfer(str(src), dfd, str(lib), 'Movie.mkv', 'copy', None, str(root)) == 'copied'
    finally:
        os.close(dfd)
    assert (lib / 'Movie.mkv').read_bytes() == b'v' * 4096
    assert foreign.read_bytes() == b'someone else'
    assert sorted(f.name for f in lib.iterdir()) == sorted(['Movie.mkv', foreign.name])


def test_a_failed_copy_removes_only_its_own_partial(tmp_path, monkeypatch):
    root = tmp_path / 'dl'
    root.mkdir()
    src = root / 'Movie.mkv'
    src.write_bytes(b'v' * 4096)
    lib = tmp_path / 'lib'
    lib.mkdir()
    foreign = lib / f'.Movie.mkv.ffffffff{lm.PARTIAL_SUFFIX}'
    foreign.write_bytes(b'someone else')

    def boom(s, d):
        os.write(d, b'half')
        raise OSError(5, 'io')
    monkeypatch.setattr(lm, '_paced_copy', boom)
    dfd = _dirfd(lib)
    try:
        with pytest.raises(OSError):
            lm.transfer(str(src), dfd, str(lib), 'Movie.mkv', 'copy', None, str(root))
    finally:
        os.close(dfd)
    assert [f.name for f in lib.iterdir()] == [foreign.name]


def test_a_partial_swapped_mid_copy_is_not_deleted(tmp_path, monkeypatch):
    root = tmp_path / 'dl'
    root.mkdir()
    src = root / 'Movie.mkv'
    src.write_bytes(b'v' * 4096)
    lib = tmp_path / 'lib'
    lib.mkdir()

    def swap(s, d):
        (part,) = [f for f in lib.iterdir() if f.name.endswith(lm.PARTIAL_SUFFIX)]
        part.unlink()
        part.write_bytes(b'not ours')               # someone put a new file at that name
        raise OSError(5, 'io')
    monkeypatch.setattr(lm, '_paced_copy', swap)
    dfd = _dirfd(lib)
    try:
        with pytest.raises(OSError):
            lm.transfer(str(src), dfd, str(lib), 'Movie.mkv', 'copy', None, str(root))
    finally:
        os.close(dfd)
    (left,) = list(lib.iterdir())
    assert left.read_bytes() == b'not ours'


# ------------------------------------------------------------- no sign-in without a downloads folder

def test_no_qbit_sign_in_while_the_downloads_folder_is_missing(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'x')
    os.rmdir(env.dl / 'incomplete')
    os.rmdir(env.dl)
    out = lm.tick(now=1000, session_factory=env.q.factory)
    assert env.q.sessions == 0 and env.q.calls == []
    assert out['checked'] == 1
    assert fm.load()['jobs'][H1]['pending_since'] == 1000
    lm.tick(now=1000 + lm.NOT_FOUND_AFTER_S, session_factory=env.q.factory)
    assert env.q.sessions == 0
    assert fm.load()['jobs'][H1]['status'] == lm.FILES_NOT_FOUND
    v = fm.view()
    assert v['downloads'] == {'path': str(env.dl), 'status': 'missing'}
    assert v['not_found'] == [{'title': fm.load()['jobs'][H1]['title'], 'since': 1000, 'reason': 'not_found'}]


def test_view_reports_a_readable_downloads_folder(env):
    assert fm.view()['downloads'] == {'path': str(env.dl), 'status': 'ok'}
    assert fm.view()['not_found'] == []


# ------------------------------------------------------------- QA-TV2/TV1 PUT rules

def test_switching_on_an_unusable_tracker_is_refused_with_its_reason(sec):
    body = lambda i: {'trackers': [{'id': i, 'enabled': True}]}   # noqa: E731
    assert st.check_put('tv', body(4), RECS) == (None, 'Delta is off in Trackers')
    assert st.check_put('tv', body(3), RECS) == (None, 'Charlie has no TV category')
    assert st.check_put('movies', body(1), RECS) == (None, 'Alpha has no Movies category')


def test_switching_an_unusable_tracker_off_is_always_allowed(sec):
    new, err = st.check_put('tv', {'trackers': [{'id': 4, 'enabled': False}, {'id': 3, 'enabled': False}]},
                            RECS, {'order': [4, 3], 'enabled': [3, 4]})
    assert err is None and new == {'order': [4, 3], 'enabled': []}
    # and once off, it can't be switched back on while unusable
    assert st.check_put('tv', {'trackers': [{'id': 4, 'enabled': True}]}, RECS, new)[1] == 'Delta is off in Trackers'


def test_a_saved_on_tracker_becomes_active_when_usable_again(sec, monkeypatch):
    st.save({'sections': {'tv': {'order': [4], 'enabled': [4]}}})
    assert [(r['id'], r['enabled'], r['active']) for r in st.arrange('tv') if r['id'] == 4] == [(4, True, False)]
    assert 4 not in st.ordered_ids('tv')
    recs = [dict(r, enabled=True) if r['id'] == 4 else dict(r) for r in RECS]
    monkeypatch.setitem(st._cfg, 'list_trackers', lambda: recs)
    assert [(r['id'], r['enabled'], r['active']) for r in st.arrange('tv') if r['id'] == 4] == [(4, True, True)]
    assert st.ordered_ids('tv') == [4]
