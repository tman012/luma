"""What You're Missing served-rail cache: instant serve, invalidation, refresh."""
import threading

import pytest

from app import daily_missing as dm


@pytest.fixture(autouse=True)
def rail_env(monkeypatch):
    dm.clear_rail_cache()
    monkeypatch.setattr(dm, '_last_library_signature', None)
    calls = {'n': 0, 'spawned': []}

    def fake_get_rail(limit=dm.RAIL_SIZE, refresh_owned=True):
        calls['n'] += 1
        return {'rail_id': dm.RAIL_ID, 'day': dm._today_ny(), 'movies': [{'tmdb_id': calls['n']}] * 0,
                'version': calls['n'], 'limit': limit}

    monkeypatch.setattr(dm, 'get_rail', fake_get_rail)
    # Queue background work so each test can run it when it chooses.
    monkeypatch.setattr(dm, '_spawn', lambda fn: calls['spawned'].append(fn))
    yield calls
    dm.clear_rail_cache()


def run_spawned(calls):
    while calls['spawned']:
        calls['spawned'].pop(0)()


def test_first_call_computes_then_serves_from_cache(rail_env):
    first = dm.get_rail_cached(20)
    assert first['version'] == 1
    for _ in range(5):
        assert dm.get_rail_cached(20)['version'] == 1
    assert rail_env['n'] == 1
    assert rail_env['spawned'] == []


def test_limits_are_cached_separately(rail_env):
    assert dm.get_rail_cached(20)['limit'] == 20
    assert dm.get_rail_cached(10)['limit'] == 10
    assert rail_env['n'] == 2


def test_invalidate_serves_stale_then_refreshes_in_background(rail_env):
    dm.get_rail_cached(20)
    dm.invalidate_rail_cache('test')
    # Stale copy is served immediately; rebuild is queued, not awaited.
    assert dm.get_rail_cached(20)['version'] == 1
    assert len(rail_env['spawned']) == 1
    run_spawned(rail_env)
    assert dm.get_rail_cached(20)['version'] == 2
    assert rail_env['spawned'] == []


def test_expired_entry_triggers_single_background_refresh(rail_env, monkeypatch):
    dm.get_rail_cached(20)
    now = dm.time.time()
    monkeypatch.setattr(dm.time, 'time', lambda: now + dm.RAIL_CACHE_MAX_AGE_SEC + 1)
    assert dm.get_rail_cached(20)['version'] == 1
    assert dm.get_rail_cached(20)['version'] == 1
    assert len(rail_env['spawned']) == 1  # deduplicated while in flight
    run_spawned(rail_env)
    assert dm.get_rail_cached(20)['version'] == 2


def test_day_rollover_refreshes(rail_env, monkeypatch):
    dm.get_rail_cached(20)
    monkeypatch.setattr(dm, '_today_ny', lambda: '2999-01-01')
    assert dm.get_rail_cached(20)['version'] == 1
    run_spawned(rail_env)
    assert dm.get_rail_cached(20)['day'] == '2999-01-01'


def test_refresh_started_before_invalidation_is_redone(rail_env, monkeypatch):
    dm.get_rail_cached(20)
    dm.invalidate_rail_cache('first')
    job = rail_env['spawned'].pop(0)
    real = dm.get_rail
    state = {'once': False}

    def racing_get_rail(limit=20, refresh_owned=True):
        out = real(limit)
        if not state['once']:
            state['once'] = True
            dm.invalidate_rail_cache('second')  # library changed mid-build
        return out

    monkeypatch.setattr(dm, 'get_rail', racing_get_rail)
    job()
    # The build that raced the second invalidation was discarded and redone.
    assert dm.get_rail_cached(20)['version'] == 3


def test_error_payload_is_not_cached(rail_env, monkeypatch):
    monkeypatch.setattr(dm, 'get_rail', lambda limit=20, refresh_owned=True: {'error': 'boom', 'movies': []})
    assert dm.get_rail_cached(20)['error'] == 'boom'
    assert dm._rail_cache == {}


def test_library_signature_change_invalidates(rail_env):
    dm.get_rail_cached(20)
    assert dm.note_library_signature((10, 10, 1)) is False  # baseline
    assert dm.note_library_signature((10, 10, 1)) is False
    assert rail_env['spawned'] == []
    assert dm.note_library_signature((11, 11, 2)) is True
    run_spawned(rail_env)
    assert dm.get_rail_cached(20)['version'] == 2


def test_replace_rail_now_is_synchronous(rail_env):
    dm.get_rail_cached(20)
    dm.get_rail_cached(10)
    out = dm.replace_rail_now(20)
    assert out['version'] == 3
    assert dm.get_rail_cached(20)['version'] == 3
    assert len(rail_env['spawned']) == 1  # the other limit refreshes in background


def test_pool_rebuild_invalidates(rail_env, monkeypatch):
    dm.get_rail_cached(20)
    written = []
    monkeypatch.setattr(dm, '_write_pool', lambda payload: written.append(payload))
    monkeypatch.setattr(dm, '_mem_pool', None)
    dm._publish_pool({'candidates': [], 'built_at': 0})
    assert written and dm._rail_cache[20]['stale'] is True
    run_spawned(rail_env)
    assert dm.get_rail_cached(20)['version'] == 2


def test_concurrent_reads_never_block_on_refresh(rail_env, monkeypatch):
    dm.get_rail_cached(20)
    gate = threading.Event()
    monkeypatch.setattr(dm, 'get_rail', lambda limit=20, refresh_owned=True: (gate.wait(5), {'day': dm._today_ny(), 'version': 99})[1])
    monkeypatch.setattr(dm, '_spawn', lambda fn: threading.Thread(target=fn, daemon=True).start())
    dm.invalidate_rail_cache('slow')
    for _ in range(50):
        assert dm.get_rail_cached(20)['version'] == 1
    gate.set()
    for _ in range(100):
        if dm.get_rail_cached(20)['version'] == 99:
            break
        dm.time.sleep(0.02)
    assert dm.get_rail_cached(20)['version'] == 99


def test_route_serves_cached_slate(monkeypatch):
    from app import main
    monkeypatch.setattr(dm, 'get_rail_cached', lambda limit=20: {'rail_id': dm.RAIL_ID, 'movies': [], 'cached': limit})
    client = main.app.test_client()
    resp = client.get('/api/rails/what-youre-missing?limit=12', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    if resp.status_code == 401:
        pytest.skip('route requires a session in this configuration')
    assert resp.get_json()['cached'] == 12


def test_cache_updater_signature_reports_library_change():
    from app.cache_updater import _library_signature
    a = _library_signature({'movies': {'x': 1}, 'movies_by_tmdb': {1: {}, '2': {}}})
    b = _library_signature({'movies': {'x': 1, 'y': 2}, 'movies_by_tmdb': {1: {}, 2: {}, 3: {}}})
    assert a != b
    assert a == _library_signature({'movies': {'x': 1}, 'movies_by_tmdb': {2: {}, 1: {}}})
