"""Tracker guard: the PM limits, enforced for every indexer, with the limits ON.

The fake clock replaces time.time/time.sleep inside app.indexer_api, so the
real limits (and the 2 s spacing) run without waiting.
"""

import json
import types

import pytest

import app.indexer_api as ia

HOUR, DAY = 3600, 86400


class _Resp:
    def __init__(self, items, status=200, headers=None, text=''):
        self._items, self.status_code, self.headers, self.text = items, status, headers or {}, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def json(self):
        return self._items


class _Session:
    def __init__(self):
        self.calls, self.status = [], 200

    def get(self, url, params=None, timeout=None):
        if url.endswith('/indexerstatus'):
            return _Resp(self.backoffs)
        if url.endswith('/api/v1/indexer'):
            return _Resp(self.indexers)
        self.calls.append(params)
        return _Resp([{'title': f'r{len(self.calls)}'}], self.status, self.headers, self.body)

    backoffs = []
    headers = {}
    indexers = []
    body = ''


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += max(0.0, s)

    def advance(self, s):
        self.now += s


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(ia, 'time', types.SimpleNamespace(time=c.time, sleep=c.sleep))
    return c


@pytest.fixture
def state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv('LUMA_STATE_DIR', str(tmp_path))
    ia._guard_reset_memory()
    for name in ('_query_cache', '_indexer_last_ts', '_status_cache', '_skip_logged'):
        getattr(ia, name).clear()
    yield tmp_path
    ia._guard_reset_memory()


def _client():
    c = ia.ProwlarrClient('http://prowlarr.test', 'k')
    c.session = _Session()
    c.session.backoffs = []
    return c


@pytest.fixture
def client(clock, state_dir):
    return _client()


_n = [0]


def _q():
    _n[0] += 1
    return [('query', f'q{_n[0]}'), ('type', 'search')]


def _send(c, ix, interactive, clock=None, step=0.0):
    # Background sends run with auto-grab priority so these tests measure the
    # whole bucket (the PM totals); the Explore reserve is covered in
    # tests/test_latest_budget.py.
    if clock and step:
        clock.advance(step)
    with ia.search_context(priority='grab'):
        return c._get_search(_q(), [ix], interactive=interactive)


def _restart():
    """Simulate a new process: drop in-memory state; the next call reloads it."""
    ia._guard_reset_memory()
    ia._query_cache.clear()
    ia._indexer_last_ts.clear()


def test_limits_table_has_pm_numbers():
    L = ia.GUARD_LIMITS
    assert L['interactive_per_hour'] == 40
    assert L['background_burst'] == 5
    assert L['background_refill_s'] == 180.0
    assert HOUR / L['background_refill_s'] == 20
    assert L['per_day'] == 300
    assert L['global_per_hour'] == 120
    assert L['query_ttl_background'] == 3 * HOUR
    assert L['query_ttl_interactive'] == 900


def test_interactive_40_per_hour_no_bucket(client, clock):
    for _ in range(40):          # back to back: no bucket for user searches
        _send(client, 7, True)
    with pytest.raises(ia.IndexerSkipped):
        _send(client, 7, True)
    assert len(client.session.calls) == 40
    clock.advance(HOUR + 1)
    _send(client, 7, True)
    assert len(client.session.calls) == 41


def test_background_burst_5_then_one_per_180s(client, clock):
    t0 = clock.now
    for _ in range(5):
        _send(client, 3, False)
    with pytest.raises(ia.IndexerSkipped):
        _send(client, 3, False)
    clock.now = t0 + 179                 # refill accrues from the first send
    with pytest.raises(ia.IndexerSkipped):
        _send(client, 3, False)
    clock.now = t0 + 181
    _send(client, 3, False)
    assert len(client.session.calls) == 6


def test_background_20_per_hour_steady_state(client, clock):
    for _ in range(5):
        _send(client, 3, False)
    sent = 0
    start = clock.now
    while clock.now - start < HOUR:
        clock.advance(30)
        try:
            _send(client, 3, False)
            sent += 1
        except ia.IndexerSkipped:
            pass
    assert sent == 20


def test_per_indexer_daily_cap_300(client, clock):
    sent = 0
    for _ in range(330):
        clock.advance(HOUR / 39)    # under 40/h interactive and 120/h global
        try:
            _send(client, 9, True)
            sent += 1
        except ia.IndexerSkipped:
            pass
    assert sent == 300
    with pytest.raises(ia.IndexerSkipped):
        _send(client, 9, True)


def test_global_120_per_hour_across_indexers(client, clock):
    for ix in range(1, 4):
        for _ in range(40):
            _send(client, ix, True)
    assert len(client.session.calls) == 120
    with pytest.raises(ia.IndexerSkipped):
        _send(client, 4, True)          # fresh indexer, but the global cap is hit
    with pytest.raises(ia.IndexerSkipped):
        _send(client, 5, False)         # background counts toward it too


def test_background_and_interactive_combine_for_daily_and_global(client, clock):
    for _ in range(5):
        _send(client, 2, False)
    for _ in range(40):
        _send(client, 2, True)       # bucket does not limit interactive
    with pytest.raises(ia.IndexerSkipped):
        _send(client, 2, True)
    with pytest.raises(ia.IndexerSkipped):
        _send(client, 2, False)
    hr, day, glob = ia._guard_counts(2, clock.now)
    assert (hr, day, glob) == (40, 45, 45)


def test_identical_query_served_from_cache(client, clock):
    q = [('query', 'same'), ('type', 'search')]
    a = client._get_search(q, [1], interactive=False)
    clock.advance(3 * HOUR - 5)
    assert client._get_search(q, [1], interactive=False) == a
    assert len(client.session.calls) == 1
    clock.advance(10)
    client._get_search(q, [1], interactive=False)
    assert len(client.session.calls) == 2


def test_interactive_cache_15_min(client, clock):
    q = [('query', 'user'), ('type', 'search')]
    client._get_search(q, [1], interactive=True)
    clock.advance(899)
    client._get_search(q, [1], interactive=True)
    clock.advance(2)
    client._get_search(q, [1], interactive=True)
    assert len(client.session.calls) == 2


def test_over_cap_returns_stale_cached_answer(client, clock):
    q = [('query', 'stale'), ('type', 'search')]
    first = client._get_search(q, [4], interactive=True)
    for _ in range(39):
        _send(client, 4, True)
    clock.advance(1000)                     # cache expired, cap still hit
    assert client._get_search(q, [4], interactive=True) == first


def test_backed_off_indexer_skipped(client, clock):
    client.session.backoffs = [{'indexerId': 6, 'disabledTill': '2099-01-01T00:00:00Z'}]
    with pytest.raises(ia.IndexerSkipped):
        _send(client, 6, True)
    assert client.session.calls == []


def test_unscoped_counted_and_capped(client, clock):
    for _ in range(40):
        client._get_search(_q(), None, interactive=True)
    with pytest.raises(ia.IndexerSkipped):
        client._get_search(_q(), None, interactive=True)
    assert ia._guard_counts(ia.UNSCOPED, clock.now)[0] == 40


def test_unscoped_honors_backoff(client, clock):
    client.session.backoffs = [{'indexerId': 6, 'disabledTill': '2099-01-01T00:00:00Z'}]
    with pytest.raises(ia.IndexerSkipped):
        client._get_search(_q(), None, interactive=True)
    assert client.session.calls == []


def test_unscoped_429_starts_persisted_cooldown(client, clock, state_dir):
    client.session.status = 429
    with pytest.raises(RuntimeError):
        client._get_search(_q(), None, interactive=True)
    client.session.status = 200
    with pytest.raises(ia.IndexerSkipped):
        client._get_search(_q(), None, interactive=True)
    assert len(client.session.calls) == 1
    _restart()
    c2 = _client()
    with pytest.raises(ia.IndexerSkipped):
        c2._get_search(_q(), None, interactive=True)
    clock.advance(ia.GUARD_LIMITS['cooldown_429_s'] + 1)
    c2._get_search(_q(), None, interactive=True)
    assert len(c2.session.calls) == 1


def test_all_state_persists_across_restart(client, clock, state_dir):
    for _ in range(5):
        _send(client, 3, False)          # bucket empty
    for _ in range(40):
        _send(client, 8, True)           # interactive cap hit
    raw = json.loads((state_dir / ia.GUARD_FILENAME).read_text())
    assert set(raw) == {'log', 'buckets', 'cooldown', 'streak'}
    assert len(raw['log']['8']) == 40 and raw['log']['8'][0][1] == 'i'
    assert raw['buckets']['3'][0] < 1
    _restart()
    c2 = _client()
    with pytest.raises(ia.IndexerSkipped):
        _send(c2, 3, False)
    with pytest.raises(ia.IndexerSkipped):
        _send(c2, 8, True)
    assert c2.session.calls == []


def test_save_is_atomic(client, clock, state_dir, monkeypatch):
    _send(client, 1, True)
    path = state_dir / ia.GUARD_FILENAME
    before = path.read_text()
    import os
    real = os.replace

    def fail(src, dst):
        raise OSError('disk full')

    monkeypatch.setattr(os, 'replace', fail)
    _send(client, 1, True)                   # save fails: old file intact, no temp left
    assert path.read_text() == before
    assert [p.name for p in state_dir.iterdir()] == [ia.GUARD_FILENAME]
    monkeypatch.setattr(os, 'replace', real)


def test_old_entries_dropped_on_load(clock, state_dir):
    path = state_dir / ia.GUARD_FILENAME
    path.write_text(json.dumps({'log': {'5': [[clock.now - DAY - 10, 'i'], [clock.now - 10, 'b']]}}))
    ia._guard_load()
    assert ia._guard_log[5] == [[clock.now - 10, 'b']]


def _skips(fn):
    try:
        fn()
    except ia.IndexerSkipped:
        return True
    return False


@pytest.mark.parametrize('headers', [{}, {'Retry-After': '120'}, {'Retry-After': 'HTTPDATE'}])
def test_per_indexer_429_cools_down_four_calls_one_hit(client, clock, headers):
    if headers.get('Retry-After') == 'HTTPDATE':
        from email.utils import formatdate
        headers = {'Retry-After': formatdate(clock.now + 120, usegmt=True)}
    client.session.status, client.session.headers = 429, headers
    with pytest.raises(RuntimeError):
        _send(client, 5, True)
    client.session.status, client.session.headers = 200, {}
    for _ in range(3):
        assert _skips(lambda: _send(client, 5, True))
    assert len(client.session.calls) == 1
    wait = 120 if headers else ia.GUARD_LIMITS['cooldown_429_s']
    assert ia._guard_cooldown[5] == pytest.approx(clock.now + wait, abs=5)
    clock.advance(wait + 5)
    _send(client, 5, True)
    assert len(client.session.calls) == 2


def test_per_indexer_400_cooldown_survives_restart(client, clock, state_dir):
    client.session.status = 400
    with pytest.raises(RuntimeError):
        _send(client, 5, False)
    _restart()
    c2 = _client()
    for _ in range(3):
        assert _skips(lambda: _send(c2, 5, True))
    assert c2.session.calls == []
    raw = json.loads((state_dir / ia.GUARD_FILENAME).read_text())
    assert float(raw['cooldown']['5']) > clock.now


def test_retry_after_parsing(clock):
    from email.utils import formatdate
    assert ia._retry_after_seconds('30', clock.now) == 30
    assert ia._retry_after_seconds(formatdate(clock.now + 90, usegmt=True), clock.now) == pytest.approx(90, abs=1)
    assert ia._retry_after_seconds('soon', clock.now) is None
    assert ia._retry_after_seconds('', clock.now) is None
    assert ia._retry_after_seconds('999999', clock.now) == 86400


def _assert_fail_closed(state_dir, clock, client):
    assert _skips(lambda: _send(client, 1, True))
    backups = [p.name for p in state_dir.iterdir() if '.bad-' in p.name]
    assert len(backups) == 1 and backups[0].startswith(ia.GUARD_FILENAME + '.bad-')
    assert _skips(lambda: client._get_search(_q(), None, interactive=True))
    assert client.session.calls == []
    _restart()                                   # still closed after a restart
    c2 = _client()
    assert _skips(lambda: _send(c2, 1, True))
    clock.advance(ia.FAIL_CLOSED_S + 1)
    _send(c2, 1, True)
    assert len(c2.session.calls) == 1


def test_corrupt_json_moved_aside_and_fails_closed(clock, state_dir, caplog):
    (state_dir / ia.GUARD_FILENAME).write_text('{not json')
    with caplog.at_level('ERROR'):
        _assert_fail_closed(state_dir, clock, _client())
    assert any('moved it to' in r.message for r in caplog.records)


@pytest.mark.parametrize('raw', ['[]', '"x"', '{"log": [], "buckets": {}}', '{"buckets": "5,0"}'])
def test_wrong_shape_moved_aside_and_fails_closed(clock, state_dir, raw):
    (state_dir / ia.GUARD_FILENAME).write_text(raw)
    _assert_fail_closed(state_dir, clock, _client())


def test_one_bad_entry_dropped_rest_kept(clock, state_dir, caplog):
    now = clock.now
    good = [[now - 10, 'i']] * 40
    (state_dir / ia.GUARD_FILENAME).write_text(json.dumps({
        'log': {'8': good + [['x', 'i'], [now - 5, 'zz'], None], 'bad-key': [[now, 'i']]},
        'buckets': {'3': [0.0, now], '4': 'full', '6': [99, now]},
        'cooldown': {'9': now + 600, '10': 'later'},
    }))
    c = _client()
    with caplog.at_level('WARNING'):
        assert _skips(lambda: _send(c, 8, True))   # 40 kept entries: interactive cap hit
    assert any('dropped' in r.message for r in caplog.records)
    assert not [p for p in state_dir.iterdir() if '.bad-' in p.name]
    assert len(ia._guard_log[8]) == 40
    assert ia._guard_buckets[4][0] == 0.0 and ia._guard_buckets[6][0] == 0.0   # bad buckets read as empty
    assert _skips(lambda: _send(c, 4, False))
    assert _skips(lambda: _send(c, 9, True))     # cooldown kept
    _send(c, 10, True)                           # bad cooldown dropped
    assert len(c.session.calls) == 1


def test_bad_state_never_raises(clock, state_dir):
    for raw in ('\x00\xff', '{"log": {"1": "abc"}}', '{"cooldown": {"1": [1, 2]}}', 'null'):
        (state_dir / ia.GUARD_FILENAME).write_text(raw)
        _restart()
        ia._guard_load()


def test_upgrade_batch_pauses_on_guard(tmp_path, monkeypatch):
    import sqlite3
    from app import upgrade_finder
    uf = upgrade_finder.UpgradeFinder(None, {}, cache_dir=str(tmp_path))
    with sqlite3.connect(str(uf.db_path)) as c:
        for i, t in enumerate(['A', 'B', 'C']):
            c.execute('INSERT INTO library_movies (tmdb_id, section_id, title, year, current_tier) '
                      'VALUES (?, 1, ?, 2000, 1)', (str(i + 1), t))

    class Fake:
        n = 0

        def _search_single(self, *a, **k):
            Fake.n += 1
            if Fake.n == 2:
                raise ia.IndexerSkipped('guard')
            return []

        def _parse_items(self, raw):
            return []

    monkeypatch.setattr(ia, 'get_indexer_client', lambda s: Fake())
    uf._do_batch(10)
    with sqlite3.connect(str(uf.db_path)) as c:
        searched = {r[0] for r in c.execute('SELECT tmdb_id FROM upgrade_results')}
    assert searched == {'1'}   # B and C stay unsearched for the next run


def test_nightly_and_sweep_caps():
    import inspect
    from app import upgrade_scheduler
    from app.explore_cache import ExploreCache
    assert 'max_batches = 2 ' in inspect.getsource(upgrade_scheduler)
    assert ExploreCache.__init__ and '6 * 3600' in inspect.getsource(ExploreCache.__init__)


# --- 400/429 fallback cooldowns (no Retry-After, no disabledTill) ---------

PHD = {'id': 5, 'name': 'My HD site', 'definitionName': 'privatehd', 'implementation': 'PrivateHD',
       'indexerUrls': ['https://privatehd.to/'], 'fields': []}
IPT_BY_URL = {'id': 6, 'name': 'Tracker six', 'definitionName': 'custom', 'implementation': 'Cardigann',
              'fields': [{'name': 'baseUrl', 'value': 'https://www.iptorrents.com/'}]}
GENERIC = {'id': 7, 'name': 'PrivateHD fan site', 'definitionName': 'somesite', 'implementation': 'Cardigann',
           'indexerUrls': ['https://example.org/']}


def _fail_once(client, ix, status):
    client.session.status = status
    with pytest.raises(RuntimeError):
        _send(client, ix, True)
    client.session.status = 200
    return ia._guard_cooldown[ix] - clock_now(client)


def clock_now(_):
    return ia.time.time()


@pytest.fixture
def known(client):
    client.session.indexers = [PHD, IPT_BY_URL, GENERIC]
    return client


@pytest.mark.parametrize('ix,status,expected', [
    (5, 400, HOUR),        # AvistaZ family by definition
    (6, 400, HOUR),        # IPTorrents by site URL
    (7, 400, 15 * 60),     # display name says PrivateHD; definition and URL don't
    (5, 429, HOUR), (7, 429, HOUR),
])
def test_fallback_cooldown_base(known, clock, ix, status, expected):
    assert _fail_once(known, ix, status) == pytest.approx(expected)


def test_repeats_double_up_to_cap_and_success_resets(known, clock, state_dir):
    waits = []
    for _ in range(5):
        waits.append(_fail_once(known, 5, 400))
        clock.advance(waits[-1] + 1)
    assert waits == [HOUR, 2 * HOUR, 4 * HOUR, 6 * HOUR, 6 * HOUR]
    _send(known, 5, True)                       # success ends the streak
    assert 5 not in ia._guard_streak
    assert _fail_once(known, 5, 400) == pytest.approx(HOUR)


def test_generic_400_doubles_from_15_min(known, clock):
    w1 = _fail_once(known, 7, 400)
    clock.advance(w1 + 1)
    assert (w1, _fail_once(known, 7, 400)) == (900, 1800)


def test_streak_persists_across_restart(known, clock, state_dir):
    clock.advance(_fail_once(known, 5, 400) + 1)
    clock.advance(_fail_once(known, 5, 400) + 1)
    assert json.loads((state_dir / ia.GUARD_FILENAME).read_text())['streak'] == {'5': 2}
    _restart()
    c2 = _client()
    c2.session.indexers = [PHD]
    assert _fail_once(c2, 5, 400) == pytest.approx(4 * HOUR)


def test_retry_after_wins_over_fallback(known, clock):
    known.session.headers = {'Retry-After': '60'}
    assert _fail_once(known, 5, 400) == pytest.approx(60)
    assert ia._guard_streak[5] == 1


def test_400_body_logged_truncated_and_redacted(known, clock, caplog):
    known.session.body = ('Bad request apikey=abcdef123 passkey: zz99 Authorization: Bearer tok.en '
                          'key 0123456789abcdef0123456789abcdef ' + 'x ' * 400)
    with caplog.at_level('WARNING'):
        _fail_once(known, 7, 400)
    line = next(r.getMessage() for r in caplog.records if 'answered 400' in r.getMessage())
    for secret in ('abcdef123', 'zz99', 'tok.en', '0123456789abcdef0123456789abcdef'):
        assert secret not in line
    assert 'Bad request' in line and '[redacted]' in line and line.endswith('...')
    assert len(line) < ia.LOG_BODY_MAX + 120


def test_429_body_not_logged(known, clock, caplog):
    known.session.body = 'slow down'
    with caplog.at_level('WARNING'):
        _fail_once(known, 7, 429)
    assert not any('slow down' in r.getMessage() for r in caplog.records)


def test_loaded_cooldowns_are_clamped(clock, state_dir):
    (state_dir / ia.GUARD_FILENAME).write_text(json.dumps(
        {'cooldown': {'5': 99999999999, str(ia.FAIL_CLOSED): 99999999999, '6': clock.now + 60}}))
    ia._guard_load()
    assert ia._guard_cooldown[5] == clock.now + 24 * HOUR
    assert ia._guard_cooldown[ia.FAIL_CLOSED] == clock.now + ia.FAIL_CLOSED_S
    assert ia._guard_cooldown[6] == clock.now + 60


def test_bad_streak_entries_dropped(clock, state_dir):
    (state_dir / ia.GUARD_FILENAME).write_text(json.dumps({'streak': {'5': 2, '6': 'x', '7': -1, 'z': 1}}))
    ia._guard_load()
    assert ia._guard_streak == {5: 2}
