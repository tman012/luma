"""v173 (PM backlog, prod): AutoRenamer's give-up on a Radarr download it can't match must stick.

Prod re-processed 'Atlantis: The Lost Empire 2001 720p' (qBit hash A02FBDF7...) 63 times in one night:
20 lookup misses, give up, mark processed... and the mark was lost, because the processed store was a
set trimmed to 500 with set(list(s)[-500:]) (arbitrary order: the hash just added could be the one
dropped). Fake qBit and Radarr only: nothing here reaches a real service."""
import importlib
import json
import os
import subprocess
import sys
import textwrap
from unittest import mock

import pytest

ATLANTIS = 'A02FBDF7' + '0' * 32


@pytest.fixture
def ar(monkeypatch):
    real_makedirs, real_exists = os.makedirs, os.path.exists
    with mock.patch('os.makedirs', lambda p, *a, **k: None if str(p).startswith('/app') else real_makedirs(p, *a, **k)), \
            mock.patch('os.path.exists', lambda p: False if str(p).startswith('/app') else real_exists(p)):
        sys.modules.pop('app.auto_rename', None)
        mod = importlib.import_module('app.auto_rename')
    yield mod
    sys.modules.pop('app.auto_rename', None)


def _renamer(ar, tmp_path, monkeypatch, torrents, lookups):
    r = ar.AutoRenamer.__new__(ar.AutoRenamer)
    r.radarr_processed_file = str(tmp_path / 'radarr_processed.json')
    r.tracked_downloads, r.history = {}, []
    r.load_radarr_processed()
    monkeypatch.setattr(r, '_radarr_monitoring_enabled', lambda: True, raising=False)
    monkeypatch.setattr(r, '_radarr_settings', lambda: ('http://radarr.fake:7878', 'k'), raising=False)
    monkeypatch.setattr(r, 'get_radarr_category_torrents', lambda: list(torrents), raising=False)

    def lookup(h):
        lookups.append(h)
        return None                          # 'Could not find movie in Radarr'
    monkeypatch.setattr(r, 'lookup_radarr_movie', lookup, raising=False)
    monkeypatch.setattr(r, 'process_completed_torrent', lambda *a: pytest.fail('nothing to process'), raising=False)
    return r


def _t(h, name='Atlantis The Lost Empire 2001 720p'):
    return {'hash': h.lower(), 'name': name, 'category': 'radarr', 'progress': 1.0, 'state': 'stalledUP', 'tags': ''}


def _old_hashes(n):
    return [f'{i:040X}' for i in range(1, n + 1)]


@pytest.mark.parametrize('already', [0, 499, 520, 5200])
def test_a_give_up_sticks_and_the_loop_stops(ar, tmp_path, monkeypatch, already):
    (tmp_path / 'radarr_processed.json').write_text(json.dumps(_old_hashes(already)))
    lookups = []
    r = _renamer(ar, tmp_path, monkeypatch, [_t(ATLANTIS)], lookups)
    for _ in range(200):                     # 100 minutes of 30 s loops
        r.check_radarr_downloads()
    assert len(lookups) == 20                # one give-up, then never again
    saved = json.loads((tmp_path / 'radarr_processed.json').read_text())
    assert ATLANTIS in saved and saved[-1] == ATLANTIS
    assert len(saved) <= max(already + 1, ar.PROCESSED_CAP)

    lookups2 = []                            # restart: a fresh AutoRenamer from the same file
    r2 = _renamer(ar, tmp_path, monkeypatch, [_t(ATLANTIS)], lookups2)
    for _ in range(50):
        r2.check_radarr_downloads()
    assert lookups2 == []


def test_the_trim_drops_the_oldest_and_never_one_still_in_the_category(ar, tmp_path, monkeypatch):
    old = _old_hashes(ar.PROCESSED_CAP)
    live_old = old[0]                        # the very oldest entry is still in qBit's Radarr category
    (tmp_path / 'radarr_processed.json').write_text(json.dumps(old))
    lookups = []
    r = _renamer(ar, tmp_path, monkeypatch, [_t(ATLANTIS), _t(live_old, 'Old one')], lookups)
    for _ in range(25):
        r.check_radarr_downloads()
    saved = json.loads((tmp_path / 'radarr_processed.json').read_text())
    assert len(saved) == ar.PROCESSED_CAP
    assert live_old in saved and ATLANTIS in saved
    assert old[1] not in saved               # the oldest one not in the category went
    assert lookups == [ATLANTIS] * 20        # the live old one was never looked up again


def test_the_store_survives_under_any_hash_seed(tmp_path):
    """The old trim lost the give-up under some PYTHONHASHSEEDs only; run the scenario under several."""
    script = textwrap.dedent(f'''
        import json, os, sys
        from unittest import mock
        sys.path.insert(0, {os.getcwd()!r})
        re_, rm = os.path.exists, os.makedirs
        with mock.patch('os.makedirs', lambda p, *a, **k: None if str(p).startswith('/app') else rm(p, *a, **k)), \\
                mock.patch('os.path.exists', lambda p: False if str(p).startswith('/app') else re_(p)):
            import app.auto_rename as ar
        r = ar.AutoRenamer.__new__(ar.AutoRenamer)
        r.radarr_processed_file = sys.argv[1]
        r.load_radarr_processed()
        r.radarr_processed.add({ATLANTIS!r})
        r.save_radarr_processed()
        print({ATLANTIS!r} in json.load(open(sys.argv[1])))
    ''')
    for seed in ('0', '1', '7', '42', '1234'):
        f = tmp_path / f'p{seed}.json'
        f.write_text(json.dumps(_old_hashes(6000)))
        out = subprocess.run([sys.executable, '-c', script, str(f)], capture_output=True, text=True, timeout=60,
                             env={**os.environ, 'PYTHONHASHSEED': seed, 'LUMA_STATE_DIR': str(tmp_path)})
        assert out.stdout.strip().splitlines()[-1:] == ['True'], (seed, out.stderr[-2000:])


def test_old_file_loads_and_a_plain_set_still_saves(ar, tmp_path, monkeypatch):
    (tmp_path / 'radarr_processed.json').write_text(json.dumps(['AA' * 20, 'BB' * 20]))
    r = _renamer(ar, tmp_path, monkeypatch, [], [])
    assert 'AA' * 20 in r.radarr_processed and len(r.radarr_processed) == 2
    r.radarr_processed = {'CC' * 20}             # code (or a test) that assigns a plain set
    r.radarr_processed.add('DD' * 20)
    r.save_radarr_processed()
    assert sorted(json.loads((tmp_path / 'radarr_processed.json').read_text())) == ['CC' * 20, 'DD' * 20]
    r.radarr_processed.remove('CC' * 20)
    assert 'CC' * 20 not in r.radarr_processed


def test_a_failed_write_keeps_the_previous_file(ar, tmp_path, monkeypatch):
    f = tmp_path / 'radarr_processed.json'
    f.write_text(json.dumps([ATLANTIS]))
    r = _renamer(ar, tmp_path, monkeypatch, [], [])
    r.radarr_processed.add('EE' * 20)

    def boom(*a, **k):
        raise OSError('disk full')
    monkeypatch.setattr(ar.json, 'dump', boom)
    r.save_radarr_processed()
    assert json.loads(f.read_text()) == [ATLANTIS]          # not truncated: the give-up survives
    monkeypatch.undo()


@pytest.mark.parametrize('bad', ['{"a": 1}', 'not json', '[1, null, "' + 'FF' * 20 + '"]'])
def test_a_bad_file_never_crashes(ar, tmp_path, monkeypatch, bad):
    (tmp_path / 'radarr_processed.json').write_text(bad)
    r = _renamer(ar, tmp_path, monkeypatch, [], [])
    assert all(isinstance(h, str) for h in r.radarr_processed)


def test_after_heavy_churn_in_one_process_a_new_give_up_still_sticks(ar, tmp_path, monkeypatch):
    """The prod mechanism: in a long-running process the old set trim kept evicting the lowest hash
    slot, so survivors drifted to high slots and a new hash was evicted on its own save, every time
    (simulated: ~35-45% of new hashes after 1,000 adds, and a lost one is lost on all of the next 63)."""
    r = _renamer(ar, tmp_path, monkeypatch, [], [])
    for i in range(ar.PROCESSED_CAP + 1000):
        r.radarr_processed.add(f'{i:040X}')
        if i % 50 == 0:
            r.save_radarr_processed()
    for _ in range(63):
        r.radarr_processed.add(ATLANTIS)
        r.save_radarr_processed()
        assert ATLANTIS in r.radarr_processed
    assert ATLANTIS in json.loads((tmp_path / 'radarr_processed.json').read_text())
