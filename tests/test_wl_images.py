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
    s, _ = make(tmp_path, cap=3 * 2 * (len(JPG) + len(PNG)) + 10, clock=clock)   # main + phone copy
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
    assert items[1]['wl_images'] == {'backdrop': None, 'logo': None, 'backdrop_textless': False}
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


# v348 (Designer) -----------------------------------------------------------------------------------------------
def _jpeg(w, h):
    from io import BytesIO
    from PIL import Image
    b = BytesIO(); Image.new('RGB', (w, h), (10, 20, 30)).save(b, 'JPEG'); return b.getvalue()


def test_backdrops_are_stored_at_most_1920_wide(tmp_path):
    from io import BytesIO
    from PIL import Image
    big = _jpeg(3840, 2160)
    s, _ = make(tmp_path, download=lambda u: big if u.endswith('.jpg') else PNG)
    s.sync('movie', [1]); s.drain()
    im = Image.open(s.path(('movie', 1), 'backdrop'))
    assert im.size == (1920, 1080)
    small = _jpeg(1280, 720)
    assert w.fit_backdrop(small) == small


def test_textless_flag_is_kept_with_the_backdrop_and_pruned_with_it(tmp_path, test_app, monkeypatch):
    s, _ = make(tmp_path, resolve=lambda t, i: {'backdrop': 'https://img/b.jpg', 'logo': 'https://img/l.png', 'backdrop_textless': True})
    s.sync('tv', [5]); s.drain()
    assert s.textless(('tv', 5))
    monkeypatch.setattr(m, '_wl_store_obj', s)
    items = [{'tmdb_id': 5}]
    with test_app.test_request_context('/api/watchlist/tv'):
        m._wl_sync_and_annotate('tv', items)
    assert items[0]['wl_images']['backdrop_textless'] is True
    assert all(p.name != 'textless' for _, _, p in s.files())
    s.sync('tv', [6])
    assert not (tmp_path / 'wl' / 'tv' / '5').exists()


def test_resolve_prefers_the_textless_backdrop_when_there_is_a_logo(monkeypatch):
    class R:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data
    seen = []

    def get(url, params=None, timeout=None):
        seen.append(url)
        if url.endswith('/images'):
            return R({'backdrops': [
                {'file_path': '/en.jpg', 'iso_639_1': 'en', 'vote_average': 9},
                {'file_path': '/low.jpg', 'iso_639_1': None, 'vote_average': 4},
                {'file_path': '/best.jpg', 'iso_639_1': None, 'vote_average': 7},
            ]})
        return R({'backdrop_path': '/withtext.jpg'})
    monkeypatch.setattr(m.requests, 'get', get)
    monkeypatch.setattr(m.settings, 'get', lambda k, d=None: 'k' if k == 'apis.tmdb.api_key' else d)
    monkeypatch.setattr(m, '_fetch_one_logo', lambda *a, **k: '/logo.png')
    r = m._wl_resolve('tv', 153312)
    assert r == {'backdrop': 'https://image.tmdb.org/t/p/w1920/best.jpg',
                 'logo': 'https://image.tmdb.org/t/p/original/logo.png', 'backdrop_textless': True}
    monkeypatch.setattr(m, '_fetch_one_logo', lambda *a, **k: None)   # no logo: backdrop_path, no images call
    n = len(seen)
    r = m._wl_resolve('movie', 1)
    assert r['backdrop'] == 'https://image.tmdb.org/t/p/w1920/withtext.jpg' and r['backdrop_textless'] is False
    assert not any(u.endswith('/images') for u in seen[n:])
    assert m._wl_textless([{'file_path': '/a.jpg', 'iso_639_1': 'fr'}]) is None   # no textless: backdrop_path


# v349 ---------------------------------------------------------------------------------------------------------
def _png(w, h):
    from io import BytesIO
    from PIL import Image
    b = BytesIO(); Image.new('RGBA', (w, h), (255, 0, 0, 128)).save(b, 'PNG'); return b.getvalue()


def test_phone_copies_are_stored_and_served_with_w(tmp_path, test_app, monkeypatch):
    from PIL import Image
    s, _ = make(tmp_path, download=lambda u: _jpeg(1920, 1080) if u.endswith('.jpg') else _png(800, 300))
    s.sync('movie', [11]); s.drain()
    assert Image.open(s.path(('movie', 11), 'backdrop', 1280)).size == (1280, 720)
    lg = Image.open(s.path(('movie', 11), 'logo', 300))
    assert lg.size == (300, 112) and lg.mode == 'RGBA'
    monkeypatch.setattr(m, '_wl_store_obj', s)
    c = test_app.test_client()
    r = c.get('/api/wl-image/movie/11/backdrop?w=1280')
    assert r.status_code == 200 and Image.open(__import__('io').BytesIO(r.data)).size == (1280, 720)
    assert c.get('/api/wl-image/movie/11/logo?w=300').status_code == 200
    for bad in ('/api/wl-image/movie/11/backdrop?w=780', '/api/wl-image/movie/11/backdrop?w=300',
                '/api/wl-image/movie/11/logo?w=1280', '/api/wl-image/movie/11/backdrop?w=%C2%B2'):
        assert c.get(bad).status_code == 400, bad


def test_phone_copies_are_made_for_titles_stored_before_them(tmp_path):
    s, calls = make(tmp_path, download=lambda u: _jpeg(1920, 1080) if u.endswith('.jpg') else _png(600, 200))
    s.sync('movie', [12]); s.drain()
    for k in ('backdrop', 'logo'):
        s.path(('movie', 12), k, w.VARIANTS[k]).unlink()
    n = len(calls['download'])
    s.sync('movie', [12]); s.drain()
    assert s.path(('movie', 12), 'backdrop', 1280) and s.path(('movie', 12), 'logo', 300)
    assert len(calls['download']) == n, 'made from the stored image, not downloaded again'


def test_an_empty_watchlist_answer_never_wipes_the_store(tmp_path):
    s, _ = make(tmp_path)
    s.sync('movie', [1]); s.drain()
    s.sync('movie', [])
    assert s.path(('movie', 1), 'backdrop')


def test_cap_and_removal_touch_only_the_stores_own_files(tmp_path):
    s, _ = make(tmp_path, cap=1)
    other = tmp_path / 'wl' / 'movie' / '1' / 'notes.txt'
    other.parent.mkdir(parents=True); other.write_text('keep')
    stray = tmp_path / 'wl' / 'x' / 'y' / 'z.jpg'
    stray.parent.mkdir(parents=True); stray.write_bytes(JPG)
    s.sync('movie', [1]); s.drain()
    s.enforce_cap(); s.remove(('movie', 1))
    assert other.exists() and stray.exists()


def test_minimal_mode_allows_the_endpoint_and_the_kill_switch_turns_it_off(test_app, store, monkeypatch):
    from app import minimal
    s, _ = store
    s.sync('movie', [578]); s.drain()
    c = test_app.test_client()
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    assert minimal.enabled() and minimal.path_allowed('/api/wl-image/movie/578/backdrop?w=1280'.split('?')[0])
    assert c.get('/api/wl-image/movie/578/backdrop').status_code != 404, 'past the minimal gate (sign-in applies)'
    monkeypatch.delenv('LUMA_MINIMAL')
    assert c.get('/api/wl-image/movie/578/backdrop').status_code == 200
    monkeypatch.setenv('LUMA_WL_IMAGES', '0')
    assert c.get('/api/wl-image/movie/578/backdrop').status_code == 404
    items = [{'tmdb_id': 578}]
    with test_app.test_request_context('/'):
        m._wl_sync_and_annotate('movie', items)
    assert 'wl_images' not in items[0]


def test_minimal_home_watchlist_rail_and_native_watchlist_follow_the_store(test_app, store, monkeypatch):
    from app import home_rails, movie_watchlist
    s, _ = store
    rows = [{'tmdb_id': 578, 'title': 'Jaws', 'year': 1975}, {'tmdb_id': 603, 'title': 'The Matrix', 'year': 1999}]
    monkeypatch.setattr(movie_watchlist, 'movies', lambda: [dict(r) for r in rows])
    monkeypatch.setattr(home_rails, 'WATCHLIST_LIMIT', 1)
    from app import digital_release_cache
    monkeypatch.setattr(digital_release_cache, 'enrich_watchlist_items', lambda rows: None)
    with test_app.app_context():
        out = home_rails.watchlist_items()
    s.drain()
    assert s.present(('movie', 603))['backdrop'], 'the whole watchlist is synced, not just the rail\'s cards'
    assert 'wl_images' in out[0]
    with test_app.app_context():
        out = home_rails.watchlist_items()
    assert out[0]['wl_images']['backdrop'].endswith(f"/api/wl-image/movie/{out[0]['tmdb_id']}/backdrop")
    movie_watchlist.removed_hooks[0](578) if movie_watchlist.removed_hooks else None
    assert not s.present(('movie', 578))['backdrop'], 'a native watchlist removal prunes at once'


def test_v350_a_phone_copy_not_made_yet_serves_the_stored_image_uncached(test_app, store):
    s, _ = store
    s.sync('movie', [578]); s.drain()
    s.path(('movie', 578), 'backdrop', 1280).unlink()
    r = test_app.test_client().get('/api/wl-image/movie/578/backdrop?w=1280')
    assert r.status_code == 200 and r.data == JPG and r.headers['Cache-Control'] == 'no-cache'


def test_v350_the_first_request_never_waits_on_the_reconcile(test_app, tmp_path, monkeypatch):
    import threading as th
    gate = th.Event()
    monkeypatch.setattr(w.WlImageStore, 'reconcile', lambda self: gate.wait(5))
    monkeypatch.setattr(m, '_wl_store_obj', None)
    monkeypatch.setenv('LUMA_WL_IMAGE_DIR', str(tmp_path / 'wl'))
    t0 = time.time()
    r = test_app.test_client().get('/api/wl-image/movie/578/backdrop')
    gate.set()
    assert r.status_code == 404 and time.time() - t0 < 2
    monkeypatch.setattr(m, '_wl_store_obj', None)
