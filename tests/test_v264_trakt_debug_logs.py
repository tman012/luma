"""v264 (Ops FYI): DEBUG lines for each Trakt rail fetch (rail, duration, cache hit/stale/miss) and for /api/home
rail assembly; never the Trakt client ID or any key. Trakt, TMDB and OMDb mocked."""
import logging
import time

from app import home_rails
from tests.test_minimal_trakt import CID, REMOTE, _client, _connect, minimal, _key_tests_via_requests_get  # noqa: F401
from tests.test_v255_home_trakt_rails import trakt  # noqa: F401
from tests.test_v263_trakt_rail_cache import TRAKT_IDS, _saved, _wait


def _debug(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG and r.name == 'app.home_rails']


def test_debug_lines_for_miss_fetch_hit_stale_and_assembly(minimal, trakt, caplog):  # noqa: F811
    c = _client(minimal)
    _connect(c)
    home_rails.clear_trakt_last()
    rid = TRAKT_IDS[0]
    with caplog.at_level(logging.DEBUG, logger='app.home_rails'):
        c.get('/api/home', environ_base=REMOTE)
        assert _wait(_saved)
        c.get('/api/home', environ_base=REMOTE)
        with home_rails._lock:
            home_rails._trakt_saved[rid]['at'] = time.time() - home_rails.RAIL_FRESH_S - 5
        c.get('/api/home', environ_base=REMOTE)
        assert _wait(lambda: time.time() - home_rails._entry(rid)['at'] < 60)
    lines = _debug(caplog)
    assert any(f'Trakt rail {rid}: cache miss' in m for m in lines)
    assert any(f'Trakt rail {rid}: fetched' in m and ' ms' in m for m in lines)
    assert any(f'Trakt rail {rid}: cache hit' in m for m in lines)
    assert any(f'Trakt rail {rid}: cache stale' in m for m in lines)
    assert any(m.startswith('Home: /api/home rails assembled in ') and ' ms ' in m for m in lines)
    joined = '\n'.join(r.getMessage() for r in caplog.records)
    assert CID not in joined and 'api_key' not in joined


def test_failed_fetch_logs_duration(minimal, monkeypatch, caplog):  # noqa: F811
    def boom(rid):
        raise RuntimeError('down')
    monkeypatch.setitem(home_rails._cfg, 'fetch_trakt', boom)
    home_rails.clear_trakt_last()
    with caplog.at_level(logging.DEBUG, logger='app.home_rails'):
        fut = home_rails.refresh_trakt(TRAKT_IDS[0])
        try:
            fut.result(timeout=5)
        except RuntimeError:
            pass
    assert any('fetch failed after' in m and 'RuntimeError' in m for m in _debug(caplog))
