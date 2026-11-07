"""wl-images (trial): the Watchlist image store and its endpoint."""
import os
import threading
import time
from pathlib import Path

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


def _touch_title(s, i):
    for _, _, p in s.files():
        if p.parent.name == str(i):
            s.touch(p)


def test_cap_prunes_least_recently_served_first(tmp_path):
    clock = Clock()
    jpg, png = _jpeg(80, 45), _png(40, 20)   # real images, so phone copies exist and count
    s, _ = make(tmp_path, cap=10**12, clock=clock, download=lambda u: jpg if u.endswith('.jpg') else png)
    s.sync('movie', [1]); s.drain()
    one = s.report()
    assert one['bytes'] == s.size()
    assert one['source_bytes'] > 0 and one['variant_bytes'] > 0
    assert one['bytes'] == one['source_bytes'] + one['variant_bytes']
    s.cap = one['bytes'] * 3 + 10
    for i in (1, 2, 3):
        clock.t += 10
        s.sync('movie', list(range(1, i + 1))); s.drain()
        _touch_title(s, i)
    clock.t += 10
    _touch_title(s, 1)   # 1 served again: newest of the three
    before = s.report()['bytes']
    s.sync('movie', [1, 2, 3, 4]); s.drain()
    assert s.report()['bytes'] <= s.cap
    assert s.report()['bytes'] < before + one['bytes']
    assert s.path(('movie', 1), 'backdrop') and s.path(('movie', 4), 'backdrop')
    assert s.path(('movie', 3), 'backdrop'), 'a newer title stays'
    assert not s.path(('movie', 2), 'backdrop'), 'the least recently served goes first'
    assert not (tmp_path / 'wl' / 'movie' / '2' / 'source-original').exists()


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
    assert r.headers['Cache-Control'] == 'no-store'
    assert 'immutable' not in r.headers['Cache-Control'] and 'max-age' not in r.headers['Cache-Control']
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
    assert items[0]['wl_images']['backdrop'].endswith(f"/api/wl-image/movie/578/backdrop?r={w.URL_REV}")
    assert items[0]['wl_images']['logo'].endswith(f"/api/wl-image/movie/578/logo?r={w.URL_REV}")
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
    assert "if (path.startsWith('/api/'))" in sw and 'return; // Data and proxied images: network only, never cached' in sw
    assert 'no-store|private' in sw


# v348 (Designer) -----------------------------------------------------------------------------------------------
def _jpeg(w, h):
    from io import BytesIO
    from PIL import Image
    b = BytesIO(); Image.new('RGB', (w, h), (10, 20, 30)).save(b, 'JPEG'); return b.getvalue()


def test_backdrops_keep_original_pixels_and_bytes(tmp_path):
    from PIL import Image
    big = _jpeg(3840, 2160)
    s, calls = make(tmp_path, download=lambda u: big if u.endswith('.jpg') else PNG)
    s.sync('movie', [1]); s.drain()
    stored = s.path(('movie', 1), 'backdrop').read_bytes()
    assert stored == big, 'the original is stored byte for byte, not recompressed'
    assert Image.open(s.path(('movie', 1), 'backdrop')).size == (3840, 2160)
    assert (tmp_path / 'wl' / 'movie' / '1' / 'source-original').is_file()
    n = len(calls['download'])
    s.sync('movie', [1]); s.drain()
    assert len(calls['download']) == n, 'an original already stored is not fetched again'


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
    assert r == {'backdrop': 'https://image.tmdb.org/t/p/original/best.jpg',
                 'logo': 'https://image.tmdb.org/t/p/original/logo.png', 'backdrop_textless': True}
    monkeypatch.setattr(m, '_fetch_one_logo', lambda *a, **k: None)   # no logo: backdrop_path, no images call
    n = len(seen)
    r = m._wl_resolve('movie', 1)
    assert r['backdrop'] == 'https://image.tmdb.org/t/p/original/withtext.jpg' and r['backdrop_textless'] is False
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
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    assert Image.open(__import__('io').BytesIO(r.data)).size == (1280, 720)
    logo = c.get('/api/wl-image/movie/11/logo?w=300')
    assert logo.status_code == 200 and logo.headers['Cache-Control'] == 'no-store'
    assert Image.open(__import__('io').BytesIO(logo.data)).mode == 'RGBA'
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
    assert out[0]['wl_images']['backdrop'].endswith(f"/api/wl-image/movie/{out[0]['tmdb_id']}/backdrop?r={w.URL_REV}")
    movie_watchlist.removed_hooks[0](578) if movie_watchlist.removed_hooks else None
    assert not s.present(('movie', 578))['backdrop'], 'a native watchlist removal prunes at once'


def test_v350_a_phone_copy_not_made_yet_is_built_and_not_the_original(test_app, tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    big = _jpeg(3840, 2160)
    s, _ = make(tmp_path, download=lambda u: big if u.endswith('.jpg') else _png(800, 300))
    s.sync('movie', [578]); s.drain()
    s.path(('movie', 578), 'backdrop', 1280).unlink()
    monkeypatch.setattr(m, '_wl_store_obj', s)
    r = test_app.test_client().get('/api/wl-image/movie/578/backdrop?w=1280')
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    assert r.data != big
    assert Image.open(BytesIO(r.data)).size == (1280, 720)
    assert s.path(('movie', 578), 'backdrop', 1280), 'the phone copy is written for the next request'


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


# originals (the w1920 source/desktop ceiling is gone) --------------------------------------------------------
def test_phone_logo_variant_keeps_transparency(tmp_path):
    from PIL import Image
    logo = _png(600, 200)   # half-transparent red, so a resize must not flatten onto black
    s, _ = make(tmp_path, download=lambda u: _jpeg(3840, 2160) if u.endswith('.jpg') else logo)
    s.sync('movie', [3]); s.drain()
    assert s.path(('movie', 3), 'logo').read_bytes() == logo
    im = Image.open(s.path(('movie', 3), 'logo', 300))
    assert im.mode == 'RGBA' and im.size[0] == 300
    assert im.getpixel((8, 50))[3] < 200, 'the translucent logo stays translucent'
    assert im.getpixel((8, 50))[3] > 0


def test_capped_backdrop_is_replaced_by_the_original_once(tmp_path):
    from PIL import Image
    capped = _jpeg(1920, 1080)
    original = _jpeg(3840, 2160)
    logo = _png(400, 160)
    served = {'backdrop': capped}

    def dl(u):
        return served['backdrop'] if u.endswith('.jpg') else logo
    s, calls = make(tmp_path, download=dl)
    s.sync('movie', [9]); s.drain()
    # What the previous ceiling left on disk: a 1920 file and no original mark.
    src = s.path(('movie', 9), 'backdrop')
    src.write_bytes(capped)
    (tmp_path / 'wl' / 'movie' / '9' / 'source-original').unlink()
    n_logo = sum(1 for u in calls['download'] if u.endswith('.png'))
    n = len(calls['download'])
    served['backdrop'] = original
    s.sync('movie', [9]); s.drain()
    assert len(calls['download']) == n + 1, 'only the backdrop is fetched again'
    assert sum(1 for u in calls['download'] if u.endswith('.png')) == n_logo
    stored = s.path(('movie', 9), 'backdrop').read_bytes()
    assert stored == original and Image.open(s.path(('movie', 9), 'backdrop')).size == (3840, 2160)
    assert Image.open(s.path(('movie', 9), 'backdrop', 1280)).size == (1280, 720)
    n = len(calls['download'])
    s.sync('movie', [9]); s.drain()
    assert len(calls['download']) == n


def test_desktop_source_is_the_original_and_phone_width_never_is(test_app, tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    big = _jpeg(3840, 2160)
    s, _ = make(tmp_path, download=lambda u: big if u.endswith('.jpg') else _png(800, 300))
    s.sync('movie', [4]); s.drain()
    monkeypatch.setattr(m, '_wl_store_obj', s)
    c = test_app.test_client()
    src = c.get('/api/wl-image/movie/4/backdrop')
    assert src.status_code == 200 and src.data == big and src.headers['Cache-Control'] == 'no-store'
    assert Image.open(BytesIO(src.data)).size == (3840, 2160)
    phone = c.get('/api/wl-image/movie/4/backdrop?w=1280')
    assert phone.status_code == 200 and phone.data != big and phone.headers['Cache-Control'] == 'no-store'
    assert Image.open(BytesIO(phone.data)).size[0] == 1280
    # An undecodable source has no phone copy and is not sent in its place.
    s.path(('movie', 4), 'backdrop', 1280).unlink()
    s.path(('movie', 4), 'backdrop').write_bytes(JPG)
    miss = c.get('/api/wl-image/movie/4/backdrop?w=1280')
    assert miss.status_code == 404 and miss.headers['Cache-Control'] == 'no-store'
    assert miss.data != JPG


def test_removal_drops_measured_bytes_and_markers_are_not_counted(tmp_path):
    s, _ = make(tmp_path, download=lambda u: _jpeg(640, 360) if u.endswith('.jpg') else _png(200, 80))
    s.sync('movie', [8]); s.drain()
    info = s.report()
    assert info['bytes'] == sum(p.stat().st_size for _, _, p in s.files())
    assert info['bytes'] > 0
    assert all(p.name != 'source-original' for _, _, p in s.files())
    s.remove(('movie', 8))
    assert s.report()['bytes'] == 0 and s.report()['files'] == 0
    assert not (tmp_path / 'wl' / 'movie' / '8').exists()


def test_noise_original_is_larger_than_the_old_1920_ceiling(tmp_path):
    """Honest sample, not a live TMDb file: noise compressed once at quality 90, then the old q86/1920 ceiling
    and the phone copy measured from that same bitmap. The store keeps the quality-90 bytes unchanged."""
    from io import BytesIO
    from PIL import Image
    im = Image.effect_noise((3840, 2160), 80).convert('RGB')
    buf = BytesIO(); im.save(buf, 'JPEG', quality=90); original = buf.getvalue()
    legacy = BytesIO()
    im.resize((1920, 1080), Image.LANCZOS).convert('RGB').save(legacy, 'JPEG', quality=86, optimize=True)
    phone = w.variant_bytes(original, 1280, keep_alpha=False)
    s, _ = make(tmp_path, download=lambda u: original if u.endswith('.jpg') else _png(500, 180))
    s.sync('tv', [1]); s.drain()
    stored = s.path(('tv', 1), 'backdrop').read_bytes()
    assert stored == original
    assert len(stored) > len(legacy.getvalue())
    assert len(phone) < len(stored)
    assert Image.open(BytesIO(phone)).size == (1280, 720)
    # Recorded for the trial note. Synthetic noise, so these are not TMDb photo sizes.
    soft = Image.effect_noise((480, 270), 40).convert('RGB').resize((3840, 2160), Image.BICUBIC)
    soft_buf = BytesIO(); soft.save(soft_buf, 'JPEG', quality=90)
    soft_legacy = BytesIO()
    soft.resize((1920, 1080), Image.LANCZOS).convert('RGB').save(soft_legacy, 'JPEG', quality=86, optimize=True)
    soft_phone = w.variant_bytes(soft_buf.getvalue(), 1280, keep_alpha=False)
    print(f"WL_SAMPLE noise original={len(stored)} legacy1920q86={len(legacy.getvalue())} phone1280={len(phone)} "
          f"logo_source={s.path(('tv', 1), 'logo').stat().st_size} "
          f"logo300={s.path(('tv', 1), 'logo', 300).stat().st_size} "
          f"title_bytes={s.report()['bytes']}")
    print(f"WL_SAMPLE soft original={len(soft_buf.getvalue())} legacy1920q86={len(soft_legacy.getvalue())} "
          f"phone1280={len(soft_phone)}")


def test_revisioned_urls_route_no_store_and_are_not_the_immutable_ones(test_app, tmp_path, monkeypatch):
    """v348–v350 published the bare path and ?w= as public, immutable. The URLs we publish now carry r= and
    still honor w. A client that requests the new URL cannot be served the old cache entry."""
    from io import BytesIO
    from PIL import Image
    big = _jpeg(3840, 2160)
    s, _ = make(tmp_path, download=lambda u: big if u.endswith('.jpg') else _png(800, 300))
    s.sync('movie', [578]); s.drain()
    monkeypatch.setattr(m, '_wl_store_obj', s)
    items = [{'tmdb_id': 578}]
    with test_app.test_request_context('/api/watchlist'):
        m._wl_sync_and_annotate('movie', items)
    published = items[0]['wl_images']['backdrop']
    legacy = '/api/wl-image/movie/578/backdrop'
    legacy_phone = legacy + '?w=1280'
    assert published.endswith(f"{legacy}?r={w.URL_REV}")
    assert published != legacy and published != legacy_phone
    assert 'w=' not in published
    src = open(os.path.join(os.path.dirname(__file__), '..', 'frontend', 'src', 'services', 'localArt.ts'),
               encoding='utf-8').read()
    assert f"WL_IMAGE_REV = '{w.URL_REV}'" in src
    c = test_app.test_client()
    rev = w.URL_REV
    desktop = c.get(f'{legacy}?r={rev}')
    assert desktop.status_code == 200 and desktop.data == big and desktop.headers['Cache-Control'] == 'no-store'
    assert Image.open(BytesIO(desktop.data)).size == (3840, 2160)
    for url in (f'{legacy}?r={rev}&w=1280', f'{legacy}?w=1280&r={rev}'):
        phone = c.get(url)
        assert phone.status_code == 200 and phone.headers['Cache-Control'] == 'no-store', url
        assert phone.data != big and Image.open(BytesIO(phone.data)).size == (1280, 720), url
    logo = c.get(f'/api/wl-image/movie/578/logo?w=300&r={rev}')
    assert logo.status_code == 200 and logo.headers['Cache-Control'] == 'no-store'
    assert Image.open(BytesIO(logo.data)).size[0] == 300
    assert c.get(f'{legacy}?r={rev}&w=780').status_code == 400
    assert c.get(f'{legacy}?r={rev}&w=1920').status_code == 400


# concurrent phone-copy publication -------------------------------------------------------------------------------
def _jpeg_rgb(w, h, rgb):
    from io import BytesIO
    from PIL import Image
    b = BytesIO(); Image.new('RGB', (w, h), rgb).save(b, 'JPEG', quality=95); return b.getvalue()


def _stored_pair(tmp_path):
    """A wide backdrop and a translucent logo, already on disk, with no network left to do."""
    backdrop = _jpeg_rgb(1600, 900, (10, 20, 30))
    logo = _png(600, 200)
    s, calls = make(tmp_path, download=lambda u: backdrop if u.endswith('.jpg') else logo)
    s.sync('movie', [41]); s.drain()
    return s, calls, ('movie', 41), backdrop, logo


def _drop_variants(s, key):
    for kind, width in w.VARIANTS.items():
        p = s.path(key, kind, width)
        if p is not None:
            p.unlink()


def _parts(root):
    return [p for p in Path(root).rglob('*') if p.is_file() and p.name.endswith('.part')]


def _assert_pair(s, key, backdrop, logo):
    from PIL import Image
    assert s.path(key, 'backdrop').read_bytes() == backdrop
    assert s.path(key, 'logo').read_bytes() == logo
    b = Image.open(s.path(key, 'backdrop', 1280))
    assert b.size == (1280, 720)
    lg = Image.open(s.path(key, 'logo', 300))
    assert lg.mode == 'RGBA' and lg.size[0] == 300
    px = lg.getpixel((4, 8))
    assert 0 < px[3] < 200
    info = s.report()
    assert info['bytes'] == s.size()
    assert info['bytes'] == info['source_bytes'] + info['variant_bytes']
    assert _parts(s.root) == []


def _run_together(n, fn):
    errors = []
    lock = threading.Lock()

    def run():
        try:
            fn()
        except Exception as e:
            with lock:
                errors.append(f'{type(e).__name__}: {e}')

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_a_too_wide_variant_is_rebuilt_and_the_original_stays(tmp_path):
    from PIL import Image
    s, _, key, backdrop, _logo = _stored_pair(tmp_path)
    slot = s.path(key, 'backdrop', 1280)
    slot.write_bytes(backdrop)   # the raw original parked in the phone slot
    assert Image.open(slot).size[0] > 1280
    p = s.ensure_variant(key, 'backdrop')
    assert p is not None and Image.open(p).size == (1280, 720)
    assert s.path(key, 'backdrop').read_bytes() == backdrop
    assert _parts(s.root) == []


def test_overlapping_replaces_use_distinct_temps(tmp_path, monkeypatch):
    """The old bug was one shared `.{kind}@{width}.part`. Both callers must still have their own temp at the
    moment of replace, and both replaces must succeed."""
    from PIL import Image
    s, _, key, backdrop, logo = _stored_pair(tmp_path)
    _drop_variants(s, key)
    n = 8
    gate = threading.Barrier(n)
    names = []
    real = w._atomic_replace

    def blocked(src, dst):
        src_p = Path(src)
        assert src_p.is_file() and src_p.stat().st_size > 0
        names.append(src_p.name)
        gate.wait(5)
        assert src_p.is_file(), 'another caller consumed this temp'
        return real(src, dst)

    monkeypatch.setattr(w, '_atomic_replace', blocked)
    errors = _run_together(n, lambda: s.ensure_variant(key, 'backdrop'))
    assert errors == []
    assert len(names) == n and len(set(names)) == n
    assert all(name.startswith('.backdrop@1280.') and name.endswith('.part') for name in names)
    assert Image.open(s.path(key, 'backdrop', 1280)).size == (1280, 720)
    monkeypatch.setattr(w, '_atomic_replace', real)
    s.ensure_variant(key, 'logo')
    _assert_pair(s, key, backdrop, logo)


def test_two_threads_forty_times_and_eight_threads_fifteen_times(tmp_path):
    """The reported race: 2 threads failed 9/40 trials and 8 threads failed 15/15, winner left intact.
    Overlap here must not raise, and must not miss, corrupt, or resize the original."""
    s, _, key, backdrop, logo = _stored_pair(tmp_path)
    for threads, trials in ((2, 40), (8, 15)):
        for _ in range(trials):
            _drop_variants(s, key)
            errors = _run_together(threads, lambda: (
                s.ensure_variant(key, 'backdrop'), s.ensure_variant(key, 'logo')))
            assert errors == [], (threads, errors)
        _assert_pair(s, key, backdrop, logo)


def test_two_stores_and_the_worker_publish_the_same_variant(tmp_path, monkeypatch):
    s, calls, key, backdrop, logo = _stored_pair(tmp_path)
    other = w.WlImageStore(s._resolve, s._download, root=s.root, cap=s.cap, clock=s._clock, start_worker=False)
    _drop_variants(s, key)
    n = len(calls['download'])
    errors = _run_together(8, lambda: (s.ensure_variant(key, 'backdrop'), other.ensure_variant(key, 'logo')))
    assert errors == []
    # Hold the worker inside the resize, then let request threads publish the same slots before it does.
    entered = threading.Event()
    release = threading.Event()
    real = w.variant_bytes
    state = {'n': 0}

    def slow(body, max_w, keep_alpha, _real=real, _state=state, _entered=entered, _release=release):
        _state['n'] += 1
        if _state['n'] == 1:
            _entered.set()
            assert _release.wait(5)
        return _real(body, max_w, keep_alpha)

    monkeypatch.setattr(w, 'variant_bytes', slow)
    s._start = True
    _drop_variants(s, key)
    s.want(key)
    assert entered.wait(5)
    errors = _run_together(8, lambda: (other.ensure_variant(key, 'backdrop'), s.ensure_variant(key, 'logo')))
    release.set()
    deadline = time.time() + 5
    while time.time() < deadline and (s._good_variant(key, 'backdrop') is None or s._good_variant(key, 'logo') is None):
        time.sleep(0.02)
    if s._worker is not None:
        s._q.put(None)
        s._worker.join(timeout=2)
    assert errors == []
    assert len(calls['download']) == n, 'overlap must not download the title again'
    _assert_pair(s, key, backdrop, logo)


def test_a_refresh_during_resize_does_not_publish_the_old_copy(tmp_path, monkeypatch):
    from PIL import Image
    old = _jpeg_rgb(1600, 900, (220, 10, 10))
    new = _jpeg_rgb(1600, 900, (10, 10, 220))
    s, _ = make(tmp_path, download=lambda u: old if u.endswith('.jpg') else _png(600, 200))
    key = ('movie', 41)
    s.sync('movie', [41]); s.drain()
    s.path(key, 'backdrop', 1280).unlink()
    entered = threading.Event()
    release = threading.Event()
    real = w.variant_bytes
    state = {'n': 0}

    def slow(body, max_w, keep_alpha):
        state['n'] += 1
        if state['n'] == 1:
            entered.set()
            assert release.wait(5)
        return real(body, max_w, keep_alpha)

    monkeypatch.setattr(w, 'variant_bytes', slow)
    held = {}

    def run():
        try:
            held['path'] = s.ensure_variant(key, 'backdrop')
        except Exception as e:
            held['err'] = e

    t = threading.Thread(target=run)
    t.start()
    assert entered.wait(5)
    src = s.path(key, 'backdrop')
    tmp = src.with_name('.backdrop.refresh')
    tmp.write_bytes(new)
    os.replace(tmp, src)
    release.set()
    t.join(5)
    assert not t.is_alive()
    assert held.get('err') is None
    assert s.path(key, 'backdrop').read_bytes() == new
    im = Image.open(s.path(key, 'backdrop', 1280))
    px = im.getpixel((8, 8))
    assert px[2] > px[0] + 40, px   # the blue refresh, not the red bytes we were resizing
    assert im.size == (1280, 720)
    assert _parts(s.root) == []


def test_remove_during_resize_is_a_miss_and_leaves_no_temp(tmp_path, monkeypatch):
    s, _, key, backdrop, _logo = _stored_pair(tmp_path)
    _drop_variants(s, key)
    entered = threading.Event()
    release = threading.Event()
    real = w.variant_bytes

    def slow(body, max_w, keep_alpha):
        entered.set()
        assert release.wait(5)
        return real(body, max_w, keep_alpha)

    monkeypatch.setattr(w, 'variant_bytes', slow)
    held = {}

    def run():
        try:
            held['path'] = s.ensure_variant(key, 'backdrop')
        except Exception as e:
            held['err'] = e

    t = threading.Thread(target=run)
    t.start()
    assert entered.wait(5)
    s.remove(key)
    release.set()
    t.join(5)
    assert held.get('err') is None
    assert held.get('path') is None
    assert not t.is_alive()
    assert s.path(key, 'backdrop') is None
    assert _parts(s.root) == []
    assert not (s.root / 'movie' / '41').exists()


def test_cap_still_bounds_a_store_published_under_overlap(tmp_path):
    from PIL import Image
    backdrop = _jpeg_rgb(1600, 900, (10, 20, 30))
    logo = _png(600, 200)
    s, _ = make(tmp_path, cap=10**12, download=lambda u: backdrop if u.endswith('.jpg') else logo)
    s.sync('movie', [1, 2, 3, 4]); s.drain()
    originals = {i: s.path(('movie', i), 'backdrop').read_bytes() for i in (1, 2, 3, 4)}
    assert all(raw == backdrop for raw in originals.values())
    s.cap = max(1, s.report()['bytes'] // 2)

    def churn():
        for i in (1, 2, 3, 4):
            _drop_variants(s, ('movie', i))
            s.ensure_variant(('movie', i), 'backdrop')
            s.ensure_variant(('movie', i), 'logo')

    errors = _run_together(4, churn)
    extra = _run_together(2, s.enforce_cap)
    assert errors == [] and extra == []
    s.enforce_cap()
    assert s.report()['bytes'] <= s.cap
    assert s.report()['bytes'] == s.size()
    for i, raw in originals.items():
        p = s.path(('movie', i), 'backdrop')
        if p is None:
            continue
        assert p.read_bytes() == raw
        v = s.path(('movie', i), 'backdrop', 1280)
        if v is not None:
            assert Image.open(v).size[0] <= 1280
    assert _parts(s.root) == []


def test_concurrent_phone_requests_are_no_store_and_not_500(test_app, tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    s, _, key, backdrop, logo = _stored_pair(tmp_path)
    monkeypatch.setattr(m, '_wl_store_obj', s)
    _drop_variants(s, key)
    for threads, url, size in (
        (2, '/api/wl-image/movie/41/backdrop?r=2&w=1280', (1280, 720)),
        (8, '/api/wl-image/movie/41/backdrop?r=2&w=1280', (1280, 720)),
        (8, '/api/wl-image/movie/41/logo?w=300&r=2', None),
    ):
        _drop_variants(s, key)
        statuses = []
        lock = threading.Lock()

        def hit(url=url, size=size):
            r = test_app.test_client().get(url)
            with lock:
                statuses.append((r.status_code, r.headers.get('Cache-Control'), r.data, size))

        errors = _run_together(threads, hit)
        assert errors == [], errors
        assert statuses and all(code == 200 and cache == 'no-store' for code, cache, _data, _size in statuses)
        for _code, _cache, data, expected in statuses:
            im = Image.open(BytesIO(data))
            if expected:
                assert im.size == expected and data != backdrop
            else:
                assert im.mode == 'RGBA' and im.size[0] == 300
                assert 0 < im.getpixel((4, 8))[3] < 200
                assert data != logo
        assert s.path(key, 'backdrop').read_bytes() == backdrop
        assert s.path(key, 'logo').read_bytes() == logo
        assert _parts(s.root) == []


def test_a_file_removed_before_the_response_is_read_is_404(test_app, tmp_path, monkeypatch):
    s, _, _key, _backdrop, _logo = _stored_pair(tmp_path)
    monkeypatch.setattr(m, '_wl_store_obj', s)
    real = w.variant_servable
    seen = {'n': 0}

    def yank(path, max_w):
        ok = real(path, max_w)
        seen['n'] += 1
        # The handler checks the file again after ensure_variant. Unlink on that check so the read misses.
        if ok and seen['n'] >= 2:
            path.unlink()
        return ok

    monkeypatch.setattr(w, 'variant_servable', yank)
    r = test_app.test_client().get('/api/wl-image/movie/41/backdrop?r=2&w=1280')
    assert r.status_code == 404 and r.headers['Cache-Control'] == 'no-store'
    assert s.path(('movie', 41), 'backdrop').read_bytes() == _backdrop
