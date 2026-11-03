"""wl-images (trial): the Watchlist image store and its endpoint."""
import os
import threading
import time

import pytest

import app.main as m
from app import wl_images as w

JPG = b'\xff\xd8\xff\xe0' + b'J' * 2000
PNG = b'\x89PNG\r\n\x1a\n' + b'P' * 500


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def make(tmp_path, cap=10**9, resolve=None, download=None, clock=None):
    calls = {'resolve': [], 'download': []}

    def r(t, i):
        calls['resolve'].append((t, i))
        return resolve(t, i) if resolve else {'backdrop': f'https://img/{t}/{i}/b.jpg', 'logo': f'https://img/{t}/{i}/l.png'}

    def d(u):
        calls['download'].append(u)
        if download:
            return download(u)
        return JPG if u.endswith('.jpg') else PNG
    s = w.WlImageStore(r, d, root=tmp_path / 'wl', cap=cap, clock=clock or Clock(), start_worker=False)
    return s, calls


def test_store_fetches_watchlist_items_and_prunes_removed(tmp_path):
    s, calls = make(tmp_path)
    s.sync('movie', [578, 603])
    s.drain()
    assert s.present(('movie', 578)) == {'backdrop': True, 'logo': True}
    assert s.path(('movie', 578), 'backdrop').suffix == '.jpg' and s.path(('movie', 578), 'logo').suffix == '.png'
    s.sync('movie', [603])       # 578 left the watchlist
    assert s.present(('movie', 578)) == {'backdrop': False, 'logo': False}
    assert not (tmp_path / 'wl' / 'movie' / '578').exists()
    assert s.present(('movie', 603))['backdrop']
    s.sync('tv', [1399]); s.drain()
    assert s.present(('movie', 603))['backdrop'], 'a TV sync never prunes movies'
    n = len(calls['download'])
    s.sync('movie', [603]); s.drain()
    assert len(calls['download']) == n, 'a stored title is not fetched again'


def test_prune_files_left_on_disk_for_titles_no_longer_wanted(tmp_path):
    s, _ = make(tmp_path)
    s.sync('movie', [1, 2]); s.drain()
    s2, _ = make(tmp_path)        # a restart: nothing in memory
    s2.sync('movie', [2])
    assert not s2.path(('movie', 1), 'backdrop') and s2.path(('movie', 2), 'backdrop')


def test_cap_prunes_least_recently_served_first(tmp_path):
    clock = Clock()
    s, _ = make(tmp_path, cap=3 * (len(JPG) + len(PNG)) + 10, clock=clock)
    for i in (1, 2, 3):
        clock.t += 10
        s.sync('movie', list(range(1, i + 1))); s.drain()
        for k in ('backdrop', 'logo'):
            s.touch(s.path(('movie', i), k))
    clock.t += 10
    s.touch(s.path(('movie', 1), 'backdrop')); s.touch(s.path(('movie', 1), 'logo'))   # 1 served again: newest
    clock.t += 10
    s.sync('movie', [1, 2, 3, 4]); s.drain()
    assert s.size() <= s.cap
    assert s.path(('movie', 1), 'backdrop') and s.path(('movie', 4), 'backdrop')
    assert not s.path(('movie', 2), 'backdrop'), 'the least recently served goes first'


def test_failures_are_retried_later_not_cached_for_good(tmp_path):
    clock = Clock()
    boom = {'on': True}

    def dl(u):
        if boom['on']:
            raise RuntimeError('upstream 500')
        return JPG if u.endswith('.jpg') else PNG
    s, calls = make(tmp_path, download=dl, clock=clock)
    s.sync('movie', [7]); s.drain()
    assert not s.present(('movie', 7))['backdrop']
    n = len(calls['download'])
    s.want(('movie', 7)); s.drain()
    assert len(calls['download']) == n, 'not hammered right after a failure'
    boom['on'] = False
    clock.t += w.FAIL_RETRY_S + 1
    s.want(('movie', 7)); s.drain()
    assert s.present(('movie', 7)) == {'backdrop': True, 'logo': True}


def test_non_images_and_titles_without_art_are_not_stored(tmp_path):
    s, _ = make(tmp_path, resolve=lambda t, i: {'backdrop': 'https://img/x.jpg', 'logo': None},
                download=lambda u: b'<html>nope</html>')
    s.sync('tv', [9]); s.drain()
    assert s.present(('tv', 9)) == {'backdrop': False, 'logo': False}
    assert not list((tmp_path / 'wl').glob('**/.*.part'))


def test_valid_ids_and_types():
    assert w.valid('movie', '578') == ('movie', 578)
    assert w.valid('tv', '1399', 'logo') == ('tv', 1399)
    for t, i, k in (('person', '1', None), ('movie', 'None', None), ('movie', '\u00b2', None), ('movie', '0', None),
                    ('movie', '-1', None), ('movie', '1', 'poster'), ('movie', '1' * 11, None), ('TV', '1', None)):
        assert w.valid(t, i, k) is None, (t, i, k)


def test_default_location_is_under_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv('LUMA_WL_IMAGE_DIR', raising=False)
    monkeypatch.setenv('LUMA_DATA_DIR', str(tmp_path))
    assert w.store_root() == tmp_path / 'wl_images'
    monkeypatch.setenv('LUMA_WL_IMAGE_DIR', str(tmp_path / 'x'))
    assert w.store_root() == tmp_path / 'x'
    monkeypatch.setenv('LUMA_WL_IMAGE_CAP_MB', '64')
    assert w.cap_bytes() == 64 * 1024 * 1024


def test_reconcile_removes_partials(tmp_path):
    s, _ = make(tmp_path)
    d = tmp_path / 'wl' / 'movie' / '5'; d.mkdir(parents=True)
    (d / '.backdrop.part').write_bytes(b'x')
    s.reconcile()
    assert not (d / '.backdrop.part').exists()


@pytest.fixture
def store(tmp_path, monkeypatch):
    s, calls = make(tmp_path)
    monkeypatch.setattr(m, '_wl_store_obj', s)
    return s, calls


def test_endpoint_serves_hits_404s_misses_fast_and_rejects_bad_ids(test_app, store):
    s, calls = store
    s.sync('movie', [578]); s.drain()
    c = test_app.test_client()
    r = c.get('/api/wl-image/movie/578/backdrop')
    assert r.status_code == 200 and r.data == JPG and r.mimetype == 'image/jpeg'
    assert 'max-age=604800' in r.headers['Cache-Control']
    assert c.get('/api/wl-image/movie/578/logo').mimetype == 'image/png'
    n = len(calls['resolve'])
    t0 = time.monotonic()
    r = c.get('/api/wl-image/movie/999/backdrop')
    assert r.status_code == 404 and time.monotonic() - t0 < 0.5
    assert r.headers['Cache-Control'] == 'no-store'
    assert len(calls['resolve']) == n, 'a miss never fetches inline'
    for bad in ('/api/wl-image/person/1/logo', '/api/wl-image/movie/None/logo', '/api/wl-image/movie/%C2%B2/logo',
                '/api/wl-image/movie/1/poster', '/api/wl-image/movie/..%2F..%2Fetc/logo'):
        assert c.get(bad).status_code in (400, 404), bad


def test_watchlist_items_name_their_local_images(test_app, store):
    s, _ = store
    s.sync('movie', [578]); s.drain()
    items = [{'tmdb_id': 578}, {'tmdb_id': 603}]
    with test_app.test_request_context('/api/watchlist'):
        m._wl_sync_and_annotate('movie', items)
    assert items[0]['wl_images']['backdrop'].endswith('/api/wl-image/movie/578/backdrop')
    assert items[0]['wl_images']['logo'].endswith('/api/wl-image/movie/578/logo')
    assert items[1]['wl_images'] == {'backdrop': None, 'logo': None}
    s.drain()
    assert s.present(('movie', 603))['backdrop'], 'a new watchlist title is fetched (background)'
    with test_app.test_request_context('/api/watchlist'):
        m._wl_sync_and_annotate('movie', [{'tmdb_id': 603}])
    assert not s.present(('movie', 578))['backdrop'], 'removed from the watchlist: pruned'


def test_background_worker_never_blocks_the_request(tmp_path):
    gate = threading.Event()

    def slow(u):
        gate.wait(5)
        return JPG
    s = w.WlImageStore(lambda t, i: {'backdrop': 'https://img/b.jpg', 'logo': None}, slow, root=tmp_path / 'wl')
    t0 = time.monotonic()
    s.sync('movie', [1])
    assert time.monotonic() - t0 < 0.5
    gate.set()
    for _ in range(50):
        if s.present(('movie', 1))['backdrop']:
            break
        time.sleep(0.05)
    assert s.present(('movie', 1))['backdrop']


def test_service_worker_never_caches_api_paths():
    sw = open(os.path.join(os.path.dirname(__file__), '..', 'static', 'sw.js'), encoding='utf-8').read()
    assert "p.startsWith('/api/')) return false" in sw
