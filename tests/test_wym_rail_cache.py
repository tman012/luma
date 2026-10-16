"""What You're Missing served-rail cache: single flight, slicing, invalidation."""
import threading
import time

import pytest

from app import daily_missing as dm


def _movies(n, tag):
    return [{'tmdb_id': i + 1, 'tag': tag} for i in range(n)]


@pytest.fixture
def rail_env(monkeypatch):
    dm.clear_rail_cache()
    monkeypatch.setattr(dm, '_last_library_signature', None)
    calls = {'n': 0, 'limits': [], 'spawned': []}

    def fake_get_rail(limit=dm.RAIL_SIZE, refresh_owned=True):
        calls['n'] += 1
        calls['limits'].append(limit)
        return {'rail_id': dm.RAIL_ID, 'day': dm._today_ny(),
                'movies': _movies(limit, calls['n']), 'version': calls['n']}

    monkeypatch.setattr(dm, 'get_rail', fake_get_rail)
    yield calls
    dm.clear_rail_cache()


@pytest.fixture
def queued(rail_env, monkeypatch):
    """Queue background work so a test can run it when it chooses."""
    monkeypatch.setattr(dm, '_spawn', lambda fn: rail_env['spawned'].append(fn))
    return rail_env


def run_spawned(calls):
    while calls['spawned']:
        calls['spawned'].pop(0)()


def test_cold_start_stampede_builds_once(rail_env, monkeypatch):
    """8 threads on a cold cache: exactly one get_rail, all get the slate."""
    gate = threading.Event()
    real = dm.get_rail
    seen = []

    def slow_get_rail(limit=dm.RAIL_SIZE, refresh_owned=True):
        gate.wait(5)
        return real(limit)

    monkeypatch.setattr(dm, 'get_rail', slow_get_rail)
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        seen.append(dm.get_rail_cached(20))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.3)  # let every thread reach the cache while the build runs
    gate.set()
    for t in threads:
        t.join(10)
    assert rail_env['n'] == 1
    assert len(seen) == 8
    assert all(p['version'] == 1 and len(p['movies']) == 20 for p in seen)


def test_single_slate_at_max_sliced_per_request(queued):
    a = dm.get_rail_cached(5)
    b = dm.get_rail_cached(40)
    c = dm.get_rail_cached(999)
    assert queued['n'] == 1
    assert queued['limits'] == [dm.RAIL_CACHE_SIZE]
    assert len(a['movies']) == 5 and len(b['movies']) == 40 and len(c['movies']) == 40
    assert a['movies'] == b['movies'][:5]


def test_serves_from_cache_without_rebuilding(queued):
    dm.get_rail_cached(20)
    for _ in range(5):
        assert dm.get_rail_cached(20)['version'] == 1
    assert queued['n'] == 1 and queued['spawned'] == []


def test_invalidate_serves_stale_then_refreshes_in_background(queued):
    dm.get_rail_cached(20)
    dm.invalidate_rail_cache('test')
    assert dm.get_rail_cached(20)['version'] == 1
    assert len(queued['spawned']) >= 1
    run_spawned(queued)
    assert dm.get_rail_cached(20)['version'] >= 2
    assert dm._rail_entry['stale'] is False


def test_expired_entry_refreshes(queued, monkeypatch):
    dm.get_rail_cached(20)
    now = dm.time.time()
    monkeypatch.setattr(dm.time, 'time', lambda: now + dm.RAIL_CACHE_MAX_AGE_SEC + 1)
    assert dm.get_rail_cached(20)['version'] == 1
    run_spawned(queued)
    assert dm.get_rail_cached(20)['version'] == 2


def test_day_rollover_refreshes(queued, monkeypatch):
    dm.get_rail_cached(20)
    monkeypatch.setattr(dm, '_today_ny', lambda: '2999-01-01')
    assert dm.get_rail_cached(20)['version'] == 1
    run_spawned(queued)
    assert dm.get_rail_cached(20)['day'] == '2999-01-01'


def test_background_refreshes_are_single_flight(rail_env, monkeypatch):
    dm.get_rail_cached(20)
    gate = threading.Event()
    real = dm.get_rail
    monkeypatch.setattr(dm, 'get_rail', lambda limit=20, refresh_owned=True: (gate.wait(5), real(limit))[1])
    for _ in range(10):
        dm.invalidate_rail_cache('burst')
        dm.get_rail_cached(20)
    gate.set()
    for _ in range(200):
        if dm._rail_inflight is None and dm.get_rail_cached(20)['version'] > 1:
            break
        time.sleep(0.01)
    # one build plus at most one redo per invalidation that raced it
    assert rail_env['n'] <= 1 + dm._BUILD_ATTEMPTS


def test_build_racing_invalidation_is_redone(queued, monkeypatch):
    dm.get_rail_cached(20)
    real = dm.get_rail
    state = {'once': False}

    def racing(limit=20, refresh_owned=True):
        out = real(limit)
        if not state['once']:
            state['once'] = True
            dm.invalidate_rail_cache('library changed mid-build')
        return out

    monkeypatch.setattr(dm, 'get_rail', racing)
    dm.invalidate_rail_cache('first')
    run_spawned(queued)
    assert dm.get_rail_cached(20)['version'] == 3
    assert dm._rail_entry['stale'] is False


def test_error_payload_is_not_cached(rail_env, monkeypatch):
    monkeypatch.setattr(dm, 'get_rail', lambda limit=20, refresh_owned=True: {'error': 'boom', 'movies': []})
    assert dm.get_rail_cached(20)['error'] == 'boom'
    assert dm._rail_entry is None


def test_replace_rail_now_waits_for_inflight_and_reflects_dismiss(rail_env, monkeypatch):
    dm.get_rail_cached(20)
    gate = threading.Event()
    real = dm.get_rail
    monkeypatch.setattr(dm, 'get_rail', lambda limit=20, refresh_owned=True: (gate.wait(5), real(limit))[1])
    bg = threading.Thread(target=lambda: dm._rebuild(block=False))
    bg.start()
    time.sleep(0.1)
    out = {}
    t = threading.Thread(target=lambda: out.update(dm.replace_rail_now(20)))
    t.start()
    time.sleep(0.1)
    gate.set()
    t.join(5)
    bg.join(5)
    # The in-flight build started before the dismiss was redone, not reused.
    assert out['version'] == 3
    assert rail_env['n'] == 3


def test_library_signature_change_invalidates(queued):
    dm.get_rail_cached(20)
    assert dm.note_library_signature((10, 10, 1)) is False
    assert dm.note_library_signature((10, 10, 1)) is False
    assert queued['spawned'] == []
    assert dm.note_library_signature((11, 11, 2)) is True
    run_spawned(queued)
    assert dm.get_rail_cached(20)['version'] == 2


def test_pool_rebuild_invalidates(queued, monkeypatch):
    dm.get_rail_cached(20)
    monkeypatch.setattr(dm, '_write_pool', lambda payload: None)
    monkeypatch.setattr(dm, '_mem_pool', None)
    dm._publish_pool({'candidates': [], 'built_at': 0})
    assert dm._rail_entry['stale'] is True
    run_spawned(queued)
    assert dm.get_rail_cached(20)['version'] == 2


def test_route_slices_cached_slate(rail_env):
    from app import main
    client = main.app.test_client()
    resp = client.get('/api/rails/what-youre-missing?limit=12', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    if resp.status_code == 401:
        pytest.skip('route requires a session in this configuration')
    assert len(resp.get_json()['movies']) == 12
    client.get('/api/rails/what-youre-missing?limit=40', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert rail_env['limits'] == [dm.RAIL_CACHE_SIZE]


def test_cache_updater_signature_reports_library_change():
    from app.cache_updater import _library_signature
    a = _library_signature({'movies': {'x': 1}, 'movies_by_tmdb': {1: {}, '2': {}}})
    b = _library_signature({'movies': {'x': 1, 'y': 2}, 'movies_by_tmdb': {1: {}, 2: {}, 3: {}}})
    assert a != b
    assert a == _library_signature({'movies': {'x': 1}, 'movies_by_tmdb': {2: {}, 1: {}}})
