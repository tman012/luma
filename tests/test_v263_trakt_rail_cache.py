"""v263: Home's Trakt rails keep their cards on disk (home_trakt_rails.json), serve saved cards at once (a stale
set refreshes in the background), keep a cold fetch going past Home's budget so the next load has it, and warm
up at boot behind the instance gate. Trakt, TMDB and OMDb are mocked."""
import json
import time

import pytest

import app.main
from app import home_rails, instance
from tests.test_minimal_trakt import CID, REMOTE, _client, _connect, minimal, _key_tests_via_requests_get  # noqa: F401
from tests.test_v255_home_trakt_rails import trakt  # noqa: F401

TRAKT_IDS = [rid for rid, _, _, _ in home_rails.TRAKT_RAILS]


def _home(c):
    return {r['id']: r['items'] for r in c.get('/api/home', environ_base=REMOTE).get_json()['rails']}


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _saved():
    return all(home_rails.trakt_last(rid) for rid in TRAKT_IDS)


@pytest.fixture
def slow(monkeypatch):
    """Wraps the real Trakt fetch (mocked HTTP) with a per-rail delay; counts fetches per rail."""
    real = home_rails._cfg['fetch_trakt']
    st = {'delay': {}, 'calls': {}}

    def fetch(rid):
        st['calls'][rid] = st['calls'].get(rid, 0) + 1
        time.sleep(st['delay'].get(rid, 0))
        return real(rid)
    monkeypatch.setitem(home_rails._cfg, 'fetch_trakt', fetch)
    return st


def _restart():
    """A new process: nothing in memory; the file in the data dir is all that's left."""
    with home_rails._lock:
        home_rails._trakt_saved.clear()
        home_rails._trakt_inflight.clear()
        home_rails._trakt_state.update(loaded=False, warm_started=False)


def test_a_cold_start_rail_is_served_within_budget_once_warmed(minimal, trakt, slow):
    c = _client(minimal)
    _connect(c)
    slow['delay'] = {rid: 0.4 for rid in TRAKT_IDS}
    assert sorted(home_rails.warm_trakt()) == sorted(TRAKT_IDS)
    assert _wait(_saved)
    before = dict(slow['calls'])
    t = time.monotonic()
    rails = _home(c)
    assert time.monotonic() - t < 0.4                       # no Trakt wait: the warmed cards
    assert all(rails[rid] for rid in TRAKT_IDS)
    assert slow['calls'] == before


def test_boot_warm_up_runs_after_its_delay_and_is_gated(minimal, trakt, slow, monkeypatch):
    c = _client(minimal)
    _connect(c)
    monkeypatch.setattr(home_rails, 'WARM_DELAY_S', 0)
    monkeypatch.setitem(home_rails._trakt_state, 'warm_started', False)
    monkeypatch.setattr(instance, 'data_dir_only', lambda job: False)
    assert home_rails.start_trakt_warm() is False and not slow['calls']
    monkeypatch.setattr(instance, 'data_dir_only', lambda job: job == 'home_trakt_rails')
    assert home_rails.start_trakt_warm() is True
    assert home_rails.start_trakt_warm() is False            # once per process
    assert _wait(_saved)
    assert 'home_trakt_rails' in instance.DATA_DIR_ONLY


def test_warm_up_skips_hidden_rails_and_a_disconnected_trakt(minimal, trakt, slow, monkeypatch):
    c = _client(minimal)
    _connect(c)
    home_rails.save_prefs({'order': [], 'hidden': ['trakt_anticipated_shows']})
    started = home_rails.warm_trakt()
    assert 'trakt_anticipated_shows' not in started and started
    _wait(lambda: not home_rails._trakt_inflight)
    monkeypatch.setitem(home_rails._cfg, 'trakt_connected', lambda: False)
    home_rails.clear_trakt_last()
    assert home_rails.warm_trakt() == []


def test_a_timed_out_rail_is_filled_on_the_next_load_from_background_work(minimal, trakt, slow, monkeypatch, caplog):
    c = _client(minimal)
    _connect(c)
    monkeypatch.setattr(home_rails, 'HOME_DEADLINE_S', 0.3)
    slow['delay'] = {'trakt_anticipated_shows': 0.8}
    t = time.monotonic()
    with caplog.at_level('INFO'):
        first = _home(c)
    assert time.monotonic() - t < 0.6                       # Home never waits past its budget
    assert 'trakt_anticipated_shows' not in first           # skipped, not an error
    assert 'rail trakt_anticipated_shows dropped (TimeoutError)' in caplog.text
    assert first['trakt_anticipated_movies']
    assert _wait(lambda: home_rails.trakt_last('trakt_anticipated_shows'))   # the fetch kept going
    second = _home(c)
    assert [x['title'] for x in second['trakt_anticipated_shows']] == [f'Ant show {i}' for i in range(3)]
    assert slow['calls']['trakt_anticipated_shows'] == 1    # filled by that same background fetch


def test_one_background_fetch_per_rail(minimal, trakt, slow, monkeypatch):
    c = _client(minimal)
    _connect(c)
    monkeypatch.setattr(home_rails, 'HOME_DEADLINE_S', 0.1)
    slow['delay'] = {'trakt_anticipated_movies': 0.6}
    _home(c)
    _home(c)
    assert _wait(lambda: home_rails.trakt_last('trakt_anticipated_movies'))
    assert slow['calls']['trakt_anticipated_movies'] == 1


def test_the_disk_cache_survives_a_restart(minimal, trakt, slow, monkeypatch):
    c = _client(minimal)
    _connect(c)
    first = _home(c)
    assert all(first[rid] for rid in TRAKT_IDS)
    path = home_rails._trakt_path()
    text = path.read_text()
    assert CID not in text                                  # cards only, never the client ID
    assert (path.stat().st_mode & 0o777) == 0o600
    assert set(json.loads(text)) == set(TRAKT_IDS)
    _restart()
    slow['delay'] = {rid: 5 for rid in TRAKT_IDS}            # Trakt would be slow: never asked
    before = dict(slow['calls'])
    t = time.monotonic()
    again = _home(c)
    assert time.monotonic() - t < 0.5
    assert {rid: again[rid] for rid in TRAKT_IDS} == {rid: first[rid] for rid in TRAKT_IDS}
    assert slow['calls'] == before


def test_stale_cards_are_served_while_one_background_refresh_runs(minimal, trakt, slow, monkeypatch):
    c = _client(minimal)
    _connect(c)
    _home(c)
    old = time.time() - home_rails.RAIL_FRESH_S - 1
    with home_rails._lock:
        for rid in TRAKT_IDS:
            home_rails._trakt_saved[rid]['at'] = old
            home_rails._trakt_saved[rid]['cards'] = [{'key': 'tmdb-movie-1', 'type': 'movie', 'title': 'Old',
                                                      'subtitle': '', 'tmdb_id': 1, 'poster': None}]
    slow['delay'] = {rid: 0.5 for rid in TRAKT_IDS}
    t = time.monotonic()
    rails = _home(c)
    assert time.monotonic() - t < 0.4
    assert all([x['title'] for x in rails[rid]] == ['Old'] for rid in TRAKT_IDS)
    assert _wait(lambda: all(home_rails._entry(rid)['at'] > old for rid in TRAKT_IDS))
    assert home_rails.trakt_last('trakt_anticipated_shows')[0]['title'] == 'Ant show 0'
    _restart()
    assert home_rails.trakt_last('trakt_anticipated_shows')[0]['title'] == 'Ant show 0'   # saved to disk too


def test_a_connector_change_deletes_the_saved_cards(minimal, trakt, slow):
    c = _client(minimal)
    _connect(c)
    _home(c)
    assert home_rails._trakt_path().exists()
    _connect(c)
    assert not home_rails._trakt_path().exists()
    _restart()
    assert all(home_rails.trakt_last(rid) is None for rid in TRAKT_IDS)


def test_a_refresh_for_a_removed_client_id_is_discarded(minimal, trakt, slow):
    c = _client(minimal)
    _connect(c)
    slow['delay'] = {'trakt_anticipated_movies': 0.4}
    fut = home_rails.refresh_trakt('trakt_anticipated_movies')
    home_rails.clear_trakt_last()
    assert fut.result(timeout=5)
    assert home_rails.trakt_last('trakt_anticipated_movies') is None
    assert not home_rails._trakt_path().exists()


def test_a_corrupt_file_is_ignored(minimal, trakt, slow):
    c = _client(minimal)
    _connect(c)
    home_rails._trakt_path().write_text('{not json')
    _restart()
    assert home_rails.trakt_last('trakt_anticipated_movies') is None
    assert _home(c)['trakt_anticipated_movies']
