"""Library mover (minimal builds): files only this install's grabs (tracked
hash AND the qBittorrent tag), never production's torrents in the shared
client; copy by default, hardlink with a copy fallback, never a move or an
overwrite; 'exists' and 'insufficient space' are recorded, not retried."""

import errno
import os
from datetime import date

import pytest

from app import file_management as fm
from app import library_mover as lm

H1 = 'a' * 40
H2 = 'b' * 40
PROD = 'c' * 40
TAG = 'luma-test'
TMDB = {'tmdb_id': 3082, 'media_type': 'movie', 'title': 'Heat', 'year': 1995}


class Settings:
    def __init__(self, **v):
        self.v = {'qbittorrent.url': 'http://qbit.invalid:8080', 'qbittorrent.username': 'u',
                  'qbittorrent.password': 'p', 'apis.tmdb.api_key': '', **v}

    def get(self, key, default=None):
        return self.v.get(key, default)


class Resp:
    def __init__(self, code=200, text='Ok.', data=None):
        self.status_code, self.text, self._data = code, text, data
        self.content, self.headers, self.encoding = text.encode(), {}, 'utf-8'

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeQbit:
    """qBittorrent: login, and torrents/info that (like a misbehaving client)
    may answer more than was asked."""

    def __init__(self):
        self.torrents = {}
        self.calls = []
        self.sessions = 0
        self.down = False
        self.extra = []

    def factory(self):
        q = self
        q.sessions += 1

        class S:
            def post(self, url, data=None, timeout=None, **kw):
                q.calls.append(('POST', url, None))
                if q.down:
                    import requests
                    raise requests.ConnectionError('down')
                return Resp()

            def get(self, url, params=None, timeout=None, **kw):
                q.calls.append(('GET', url, dict(params or {})))
                assert url.endswith('/api/v2/torrents/info'), url
                want = set((params or {}).get('hashes', '').split('|'))
                return Resp(data=[t for h, t in q.torrents.items() if h in want] + list(q.extra))

            def close(self):
                pass
        return S()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setitem(fm._cfg, 'store_path', str(tmp_path / 'store' / fm.STORE_NAME))
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    monkeypatch.setattr(lm, 'STABLE_WAIT_S', 0)
    monkeypatch.setattr(lm, 'MIN_VIDEO_BYTES', 10)
    settings = Settings()
    lm.init(settings=settings)
    dl = tmp_path / 'downloads'
    (dl / 'incomplete').mkdir(parents=True)
    media = tmp_path / 'media'
    for n in ('Movies', 'Recently Released', 'Full Rips'):
        (media / n).mkdir(parents=True)
    q = FakeQbit()

    class E:
        pass
    e = E()
    e.dl, e.media, e.q, e.settings, e.tmp = dl, media, q, settings, tmp_path
    e.tick = lambda now=None: lm.tick(now=now, session_factory=q.factory)
    return e


def add_path(name, path):
    def apply(d):
        pid = f'p_{len(d["paths"]) + 1:08x}'
        d['paths'].append({'id': pid, 'name': name, 'path': str(path)})
        return pid
    return fm.update(apply)


def torrent(env, h, rel, tags=TAG, category='luma', progress=1, state='stalledUP', save=None, **kw):
    t = {'hash': h, 'name': os.path.basename(rel), 'tags': tags, 'category': category, 'progress': progress,
         'state': state, 'save_path': str(save or env.dl) + '/', 'content_path': str((save or env.dl) / rel), **kw}
    env.q.torrents[h] = t
    return t


def movie_file(env, rel, data=b'movie-bytes' * 10):
    p = env.dl / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def job(h):
    return fm.load()['jobs'].get(h)


# ------------------------------------------------------------------ idle

def test_no_paths_never_contacts_qbit_and_moves_nothing(env):
    lm.track(H1, TMDB, 'Heat.1995.1080p.BluRay.x264')
    movie_file(env, 'Heat.1995.1080p.BluRay.x264.mkv')
    torrent(env, H1, 'Heat.1995.1080p.BluRay.x264.mkv')
    for _ in range(3):
        assert env.tick() == {'checked': 0, 'filed': 0, 'dropped': 0}
    assert env.q.sessions == 0 and env.q.calls == []
    assert job(H1)['state'] == 'waiting'
    assert not any(os.scandir(env.media / 'Movies'))


def test_nothing_waiting_never_contacts_qbit(env):
    add_path('Movies', env.media / 'Movies')
    env.tick()
    assert env.q.sessions == 0


def test_no_qbit_configured_is_quiet(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB)
    env.settings.v['qbittorrent.url'] = ''
    assert env.tick()['checked'] == 0 and env.q.sessions == 0


# ------------------------------------------------------------------ the happy path

def test_one_folder_copies_and_renames_a_finished_movie(env):
    add_path('Movies', env.media / 'Movies')
    src = movie_file(env, 'Heat.1995.1080p.BluRay.x264/Heat.1995.1080p.BluRay.x264.mkv')
    movie_file(env, 'Heat.1995.1080p.BluRay.x264/Sample/heat-sample.mkv')
    movie_file(env, 'Heat.1995.1080p.BluRay.x264/Heat.nfo')
    assert lm.track(H1.upper(), TMDB, 'Heat.1995.1080p.BluRay.x264')
    torrent(env, H1, 'Heat.1995.1080p.BluRay.x264')
    assert env.tick()['filed'] == 1
    folder = env.media / 'Movies' / 'Heat (1995)'
    dst = folder / 'Heat (1995).mkv'
    assert dst.read_bytes() == src.read_bytes()
    assert sorted(os.listdir(folder)) == ['Heat (1995).mkv']
    assert src.exists()                       # copied, never moved: it keeps seeding
    assert oct(dst.stat().st_mode & 0o777) == '0o664'
    assert oct(folder.stat().st_mode & 0o777) == '0o775'
    j = job(H1)
    assert j['state'] == 'done' and j['files'] == [{'file': 'Heat (1995).mkv', 'result': 'copied'}]
    # finished: nothing more to ask qBittorrent
    env.q.calls.clear()
    env.tick()
    assert env.q.calls == []


def test_asks_only_about_its_own_hashes_and_ignores_anything_else(env):
    """Production's finished torrents sit in the same client and folder; the
    mover never lists the client and never touches what it didn't track."""
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')
    movie_file(env, 'Prod.Movie.2020.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    prod = {'hash': PROD, 'name': 'Prod.Movie.2020.mkv', 'tags': TAG, 'category': 'luma', 'progress': 1,
            'state': 'stalledUP', 'save_path': str(env.dl), 'content_path': str(env.dl / 'Prod.Movie.2020.mkv')}
    env.q.extra = [prod]          # even if qBittorrent answers with it
    env.tick()
    gets = [c for c in env.q.calls if c[0] == 'GET']
    assert gets and all(c[2] == {'hashes': H1} for c in gets)
    assert os.listdir(env.media / 'Movies') == ['Heat (1995)']
    assert PROD not in fm.load()['jobs']


def test_tracked_but_untagged_is_production_s_and_is_dropped(env):
    """Same release already grabbed by production (the dedup path): tracked
    here, but without the tag it is never copied."""
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv', tags='', category='radarr')
    assert env.tick()['dropped'] == 1
    assert job(H1) is None
    assert not any(os.scandir(env.media / 'Movies'))


def test_category_named_like_the_tag_counts_as_ours(env, monkeypatch):
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'qa-box')
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv', tags='other, stuff', category='QA-box')
    assert env.tick()['filed'] == 1


def test_another_tag_name_is_not_ours(env, monkeypatch):
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'qa-box')
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv', tags='luma-test')
    assert env.tick()['dropped'] == 1


def test_not_finished_waits(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv', progress=0.5, state='downloading')
    env.tick()
    torrent(env, H1, 'Heat.1995.mkv', progress=1, state='checkingUP')
    env.tick()
    assert job(H1)['state'] == 'waiting' and not any(os.scandir(env.media / 'Movies'))


# ------------------------------------------------------------------ orphans

def test_removed_from_qbit_is_dropped_after_a_few_checks(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    for i in range(lm.ORPHAN_MISSES - 1):
        env.tick()
        assert job(H1)['misses'] == i + 1
    env.tick()
    assert job(H1) is None


def test_qbit_down_is_not_a_miss(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    env.q.down = True
    for _ in range(lm.ORPHAN_MISSES + 1):
        env.tick()
    assert job(H1)['misses'] == 0 and job(H1)['state'] == 'waiting'


def test_a_returning_torrent_resets_the_miss_count(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    env.tick()
    torrent(env, H1, 'Heat.1995.mkv', progress=0.2, state='downloading')
    env.tick()
    assert job(H1)['misses'] == 0


# ------------------------------------------------------------------ incomplete and unsafe sources

def test_never_processes_incomplete(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'incomplete/Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'incomplete/Heat.1995.mkv')
    env.tick()
    assert job(H1)['state'] == 'waiting' and not any(os.scandir(env.media / 'Movies'))


def test_never_processes_qbit_temp_folder_or_part_files(env, tmp_path):
    add_path('Movies', env.media / 'Movies')
    temp = tmp_path / 'qbt-temp'
    temp.mkdir()
    (temp / 'Heat.1995.mkv').write_bytes(b'x' * 50)
    lm.track(H1, TMDB, 'Heat.1995')
    env.q.torrents[H1] = {'hash': H1, 'name': 'Heat', 'tags': TAG, 'progress': 1, 'state': 'stalledUP',
                          'save_path': str(env.dl), 'content_path': str(temp / 'Heat.1995.mkv'),
                          'download_path': str(temp)}
    env.tick()
    assert job(H1)['state'] == 'waiting'
    movie_file(env, 'Heat.1995/Heat.1995.mkv.!qB')
    movie_file(env, 'Heat.1995/Heat.1995.mkv')
    torrent(env, H1, 'Heat.1995')
    env.tick()
    assert job(H1)['state'] == 'waiting' and not any(os.scandir(env.media / 'Movies'))


def test_content_outside_the_downloads_folder_is_refused(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    env.q.torrents[H1] = {'hash': H1, 'name': 'x', 'tags': TAG, 'progress': 1, 'state': 'stalledUP',
                          'save_path': str(env.dl), 'content_path': str(env.media / 'Movies')}
    env.tick()
    assert job(H1)['state'] == 'skipped' and job(H1)['error'] == 'outside the downloads folder'


def test_system_save_path_is_refused(env):
    add_path('Movies', env.media / 'Movies')
    lm.track(H1, TMDB, 'Heat.1995')
    env.q.torrents[H1] = {'hash': H1, 'name': 'passwd', 'tags': TAG, 'progress': 1, 'state': 'stalledUP',
                          'save_path': '/etc', 'content_path': '/etc/passwd'}
    env.tick()
    assert job(H1)['state'] == 'skipped'
    assert not any(os.scandir(env.media / 'Movies'))


def test_symlinks_in_a_download_are_never_followed(env):
    add_path('Movies', env.media / 'Movies')
    d = env.dl / 'Heat.1995'
    d.mkdir()
    (d / 'Heat.1995.mkv').symlink_to('/etc/hostname')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995')
    env.tick()
    assert job(H1)['state'] == 'skipped' and job(H1)['error'] == 'no video files'
    assert not any(os.scandir(env.media / 'Movies'))


def test_symlinked_destination_folder_is_refused(env, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    add_path('Movies', env.media / 'Movies')
    (env.media / 'Movies' / 'Heat (1995)').symlink_to(outside)
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['state'] == 'skipped'
    assert os.listdir(outside) == []


def test_hostile_titles_stay_inside_the_library(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'x.mkv')
    lm.track(H1, {**TMDB, 'title': '../../../etc', 'year': '..'}, 'x')
    torrent(env, H1, 'x.mkv')
    env.tick()
    assert os.listdir(env.media / 'Movies') == ['etc']
    assert os.listdir(env.media / 'Movies' / 'etc') == ['etc.mkv']


# ------------------------------------------------------------------ exists / no overwrite

def test_existing_file_is_skipped_and_recorded(env):
    add_path('Movies', env.media / 'Movies')
    (env.media / 'Movies' / 'Heat (1995)').mkdir()
    old = env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv'
    old.write_bytes(b'keep me')
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert old.read_bytes() == b'keep me'
    assert job(H1)['state'] == 'exists'
    assert job(H1)['files'] == [{'file': 'Heat (1995).mkv', 'result': 'exists'}]


def test_existing_symlink_at_destination_is_not_written_through(env, tmp_path):
    target = tmp_path / 'victim'
    target.write_bytes(b'victim')
    add_path('Movies', env.media / 'Movies')
    (env.media / 'Movies' / 'Heat (1995)').mkdir()
    (env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv').symlink_to(target)
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert target.read_bytes() == b'victim' and job(H1)['state'] == 'exists'


def test_paced_copy_exclusive_refuses_existing_and_symlinks(tmp_path):
    from app.paced_copy import _paced_copy
    src = tmp_path / 's'
    src.write_bytes(b'abc')
    (tmp_path / 'there').write_bytes(b'old')
    with pytest.raises(FileExistsError):
        _paced_copy(str(src), str(tmp_path / 'there'), exclusive=True)
    (tmp_path / 'link').symlink_to(tmp_path / 'nowhere')
    with pytest.raises(OSError):
        _paced_copy(str(src), str(tmp_path / 'link'), exclusive=True)
    assert not (tmp_path / 'nowhere').exists()
    assert _paced_copy(str(src), str(tmp_path / 'new'), exclusive=True) == 3


# ------------------------------------------------------------------ hardlink

def test_hardlink_on_the_same_filesystem(env):
    add_path('Movies', env.media / 'Movies')
    fm.update(lambda d: d.update(transfer='hardlink'))
    src = movie_file(env, 'Heat.1995.mkv')
    mode = src.stat().st_mode
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    dst = env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv'
    assert dst.stat().st_ino == src.stat().st_ino
    assert src.stat().st_mode == mode               # the seeding file's modes are left alone
    assert job(H1)['files'][0]['result'] == 'linked'


def test_hardlink_falls_back_to_copy(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    fm.update(lambda d: d.update(transfer='hardlink'))
    src = movie_file(env, 'Heat.1995.mkv')
    real_link = os.link
    calls = []

    def link(a, b, *args, **kw):
        calls.append(a)
        if len(calls) == 1:
            raise OSError(errno.EXDEV, 'cross-device')
        return real_link(a, b, *args, **kw)
    monkeypatch.setattr(lm.os, 'link', link)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    dst = env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv'
    assert dst.read_bytes() == src.read_bytes() and dst.stat().st_ino != src.stat().st_ino
    assert job(H1)['files'][0]['result'] == 'copied'
    assert not [f for f in os.listdir(dst.parent) if 'partial' in f]


def test_copy_lands_even_where_links_are_unsupported(env, monkeypatch):
    """CIFS: os.link refuses even the temp-to-final step; a rename finishes it."""
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')

    def no_links(*a, **k):
        raise OSError(errno.EPERM, 'no links here')
    monkeypatch.setattr(lm.os, 'link', no_links)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    folder = env.media / 'Movies' / 'Heat (1995)'
    assert os.listdir(folder) == ['Heat (1995).mkv'] and job(H1)['state'] == 'done'


# ------------------------------------------------------------------ ownership

def test_as_root_files_get_the_library_folder_s_owner(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')
    st = os.stat(env.media / 'Movies')
    chowned = []
    monkeypatch.setattr(lm.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(lm.os, 'chown', lambda p, u, g, follow_symlinks=True: chowned.append(
        (os.path.basename(p), u, g, follow_symlinks)))
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert ('Heat (1995)', st.st_uid, st.st_gid, False) in chowned
    assert any(n.endswith('.luma-partial') and (u, g) == (st.st_uid, st.st_gid) for n, u, g, _f in chowned)
    assert job(H1)['state'] == 'done'


def test_not_root_leaves_ownership_alone(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')
    monkeypatch.setattr(lm.os, 'geteuid', lambda: 1000)

    def boom(*a, **k):
        raise AssertionError('chown must not run when not root')
    monkeypatch.setattr(lm.os, 'chown', boom)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['state'] == 'done'


def test_never_chmods_777(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')
    modes = []
    real = os.chmod
    monkeypatch.setattr(lm.os, 'chmod', lambda p, m, *a, **k: (modes.append(m), real(p, m))[1])
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert {0o775, 0o664} <= set(modes) and not [m for m in modes if m & 0o002]   # never world-writable
    assert oct(os.stat(env.media / 'Movies').st_mode & 0o777) != '0o777'


# ------------------------------------------------------------------ space and failures

def test_insufficient_space_is_recorded_once(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')
    monkeypatch.setattr(lm, 'free_bytes', lambda p: 1024)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['state'] == 'skipped' and job(H1)['error'] == 'insufficient space'
    assert not any(os.scandir(env.media / 'Movies'))
    env.q.calls.clear()
    env.tick()
    assert env.q.calls == []          # not retried


def test_disk_full_mid_copy_is_insufficient_space(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')

    def full(*a, **k):
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(lm, '_paced_copy', full)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['error'] == 'insufficient space' and job(H1)['state'] == 'skipped'
    assert not [f for f in os.listdir(env.media / 'Movies' / 'Heat (1995)')]


def test_hardlink_needs_no_space(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    fm.update(lambda d: d.update(transfer='hardlink'))
    movie_file(env, 'Heat.1995.mkv')
    monkeypatch.setattr(lm, 'free_bytes', lambda p: 0)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['files'][0]['result'] == 'linked'


def test_io_errors_retry_then_fail(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')

    def eio(*a, **k):
        raise OSError(errno.EIO, 'I/O error')
    monkeypatch.setattr(lm, '_paced_copy', eio)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    for i in range(lm.MAX_ATTEMPTS - 1):
        env.tick()
        assert job(H1)['state'] == 'waiting' and job(H1)['attempts'] == i + 1
    env.tick()
    assert job(H1)['state'] == 'failed' and job(H1)['error'] == 'EIO'


def test_size_mismatch_is_a_failure_not_a_file(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.mkv')

    def short(src, dst, exclusive=False, on_progress=None):
        with open(dst, 'wb') as f:
            f.write(b'x')
        return 1
    monkeypatch.setattr(lm, '_paced_copy', short)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['attempts'] == 1
    assert os.listdir(env.media / 'Movies' / 'Heat (1995)') == []


# ------------------------------------------------------------------ rules, TMDB, map, parts, TV

def _rules(env):
    recent = add_path('Recent', env.media / 'Recently Released')
    rips = add_path('Full rips', env.media / 'Full Rips')
    older = add_path('Movies', env.media / 'Movies')
    fm.update(lambda d: d.update(rules=[
        {'id': 'r_00000001', 'enabled': True, 'path_id': recent, 'released_within': {'n': 1, 'unit': 'years'},
         'resolution': [], 'source': []},
        {'id': 'r_00000002', 'enabled': True, 'path_id': rips, 'released_within': None,
         'resolution': ['1080p'], 'source': ['remux']}], fallback_path_id=older))


def test_rules_use_the_tmdb_release_date(env, monkeypatch):
    _rules(env)
    env.settings.v['apis.tmdb.api_key'] = 'k'
    asked = []

    def get(url, params=None, timeout=None):
        asked.append(url)
        return Resp(data={'release_date': date.today().replace(day=1).isoformat()})
    monkeypatch.setattr(lm.requests, 'get', get)
    movie_file(env, 'New.2026.1080p.BluRay.REMUX.mkv')
    lm.track(H1, {**TMDB, 'title': 'New', 'year': 2026}, 'New.2026.1080p.BluRay.REMUX')
    torrent(env, H1, 'New.2026.1080p.BluRay.REMUX.mkv')
    env.tick()
    assert asked == ['https://api.themoviedb.org/3/movie/3082']
    assert os.listdir(env.media / 'Recently Released') == ['New (2026)']


def test_remux_rule_and_fallback(env, monkeypatch):
    _rules(env)
    env.settings.v['apis.tmdb.api_key'] = 'k'
    monkeypatch.setattr(lm.requests, 'get', lambda *a, **k: Resp(data={'release_date': '1995-12-15'}))
    movie_file(env, 'Heat.1995.1080p.BluRay.REMUX.mkv')
    movie_file(env, 'Heat.1995.1080p.WEB-DL.mkv')
    lm.track(H1, TMDB, 'Heat.1995.1080p.BluRay.REMUX')
    lm.track(H2, TMDB, 'Heat.1995.1080p.WEB-DL')
    torrent(env, H1, 'Heat.1995.1080p.BluRay.REMUX.mkv')
    torrent(env, H2, 'Heat.1995.1080p.WEB-DL.mkv')
    env.tick()
    assert os.listdir(env.media / 'Full Rips') == ['Heat (1995)']
    assert os.listdir(env.media / 'Movies') == ['Heat (1995)']
    assert job(H1)['release_date'] == '1995-12-15'


def test_tmdb_down_waits_then_files_without_a_date(env, monkeypatch):
    import requests
    _rules(env)
    env.settings.v['apis.tmdb.api_key'] = 'k'

    def down(*a, **k):
        raise requests.ConnectionError('down')
    monkeypatch.setattr(lm.requests, 'get', down)
    movie_file(env, 'Heat.1995.720p.WEB.mkv')
    lm.track(H1, TMDB, 'Heat.1995.720p.WEB')
    torrent(env, H1, 'Heat.1995.720p.WEB.mkv')
    t0 = job(H1)['added']
    env.tick(now=t0 + 10)
    assert job(H1)['state'] == 'waiting'
    env.tick(now=t0 + lm.RELEASE_WAIT_S + 1)
    assert job(H1)['state'] == 'done' and os.listdir(env.media / 'Movies') == ['Heat (1995)']


def test_no_age_rule_never_calls_tmdb(env, monkeypatch):
    add_path('Movies', env.media / 'Movies')
    env.settings.v['apis.tmdb.api_key'] = 'k'
    monkeypatch.setattr(lm.requests, 'get', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no TMDB')))
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['state'] == 'done'


def test_path_map_for_other_setups(env):
    add_path('Movies', env.media / 'Movies')
    fm.update(lambda d: d.update(path_map=[{'remote': '/data/torrents', 'local': str(env.dl)}]))
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    env.q.torrents[H1] = {'hash': H1, 'name': 'Heat.1995.mkv', 'tags': TAG, 'progress': 1, 'state': 'pausedUP',
                          'save_path': '/data/torrents/', 'content_path': '/data/torrents/Heat.1995.mkv'}
    env.tick()
    assert job(H1)['state'] == 'done'


def test_downloads_overlapping_a_library_path_is_refused(env):
    add_path('Everything', env.dl)
    movie_file(env, 'Heat.1995.mkv')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995.mkv')
    env.tick()
    assert job(H1)['state'] == 'skipped'


def test_multi_part_movies_get_part_numbers(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995/Heat.1995.CD1.mkv', b'1' * 100)
    movie_file(env, 'Heat.1995/Heat.1995.CD2.mkv', b'2' * 90)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995')
    env.tick()
    assert sorted(os.listdir(env.media / 'Movies' / 'Heat (1995)')) == ['Heat (1995) - pt1.mkv', 'Heat (1995) - pt2.mkv']


def test_main_file_wins_over_small_extras(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995/Heat.1995.mkv', b'1' * 1000)
    movie_file(env, 'Heat.1995/Bonus.Interview.mkv', b'2' * 100)
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995')
    env.tick()
    assert os.listdir(env.media / 'Movies' / 'Heat (1995)') == ['Heat (1995).mkv']


def test_rar_only_waits_for_the_unpack(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995/heat.rar')
    movie_file(env, 'Heat.1995/heat.r00')
    lm.track(H1, TMDB, 'Heat.1995')
    torrent(env, H1, 'Heat.1995')
    env.tick()
    assert job(H1)['state'] == 'waiting'


def test_tv_is_a_later_pass(env):
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Show.S01/Show.S01E01.mkv')
    lm.track(H1, {'tmdb_id': 1, 'media_type': 'tv', 'title': 'Show'}, 'Show.S01')
    torrent(env, H1, 'Show.S01')
    env.tick()
    assert job(H1)['state'] == 'skipped' and job(H1)['error'] == 'tv'
    assert not any(os.scandir(env.media / 'Movies'))


# ------------------------------------------------------------------ tracking

def test_track_checks_its_input(env):
    assert not lm.track('not-a-hash', TMDB)
    assert not lm.track(H1, None)
    assert not lm.track('', TMDB)
    assert lm.track('A' * 64, TMDB)               # v2 hashes too
    assert fm.load()['jobs'].keys() == {'a' * 64}


def test_track_caps_waiting_jobs(env, monkeypatch):
    monkeypatch.setattr(lm, 'MAX_JOBS_WAITING', 3)
    for i in range(5):
        lm.track(f'{i:040x}', TMDB)
    assert len(fm.load()['jobs']) == 3


def test_finished_jobs_are_pruned(env, monkeypatch):
    monkeypatch.setattr(fm, 'JOBS_KEEP', 2)
    for i in range(4):
        h = f'{i:040x}'
        lm.track(h, TMDB)
        lm._set_job(h, state='done', finished=i)
    assert sorted(fm.load()['jobs']) == [f'{2:040x}', f'{3:040x}']


def test_regrab_of_a_finished_job_tracks_again(env):
    lm.track(H1, TMDB)
    lm._set_job(H1, state='exists', finished=1)
    lm.track(H1, TMDB)
    assert job(H1)['state'] == 'waiting'


def test_start_is_idempotent(monkeypatch):
    started = []

    class T:
        def __init__(self, target=None, name=None, daemon=None):
            started.append(name)

        def start(self):
            pass

        def is_alive(self):
            return True
    monkeypatch.setattr(lm, '_thread', None)
    monkeypatch.setattr(lm.threading, 'Thread', T)
    assert lm.start() is True
    assert lm.start() is False
    assert started == ['library-mover']
