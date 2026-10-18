"""v166: a matcher-rule change re-runs review rows on the next boot (X-Men on :5002 stayed in review
because the marker still said 'v161a'); the matcher's answers don't depend on PYTHONHASHSEED."""
import json
import os
import subprocess
import sys

import pytest

from app import tv_match, tv_scan, tv_store
from tests.test_file_management import minimal as minimal_app, store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def _review_row(folder, reason='not_exact', tier='none', pick=None, ts=1500.0):
    with tv_store.write() as conn:
        conn.execute('INSERT INTO unmatched (path_id, folder, guess_title, guess_year, candidates, reason, ts, tier, pick)'
                     ' VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)', (PID, folder, folder, '[]', reason, ts, tier, pick))


@pytest.fixture
def old_rule_rows(lib, monkeypatch, fake_tmdb):  # noqa: F811
    """:5002's shape: shows imported, then review rows written AFTER the old rule's marker (so under
    that rule): one today's matcher resolves (Taskmaster, our X-Men), one it doesn't, one duplicate."""
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', True)
    tv_scan.scan_all(now=400.0)
    with tv_store.write() as conn:
        conn.execute('DELETE FROM unmatched')
        conn.execute("DELETE FROM series WHERE folder LIKE 'Taskmaster%'")    # the tree's own copy
    for name in ('Taskmaster', 'Nothing Like It', 'Severance copy'):
        (lib / name).mkdir()
        (lib / name / f'{name}.S01E01.mkv').write_bytes(b'x' * 64)
    _review_row('Taskmaster')
    _review_row('Nothing Like It', reason='no_results')
    _review_row('Severance copy', reason='duplicate', tier='duplicate', pick=95396)
    tv_store.set_meta(tv_scan.RERUN_META, json.dumps({'rule': 'v161a', 'since': 1000.0}))
    return lib


def test_the_rule_is_the_matcher_version_plus_its_fingerprint():
    assert tv_match.RULE_VERSION == 'v166a'
    assert tv_scan.RERUN_RULE.startswith('v166a+') and tv_scan.RERUN_RULE == tv_scan._rule_id()


def test_any_matcher_change_changes_the_rule(monkeypatch):
    import inspect
    src = inspect.getsource(tv_match)
    monkeypatch.setattr(inspect, 'getsource', lambda m: src + '\n# a new rule\n')
    assert tv_scan._rule_id() != tv_scan.RERUN_RULE                       # nobody bumped the name: still new
    monkeypatch.setattr(inspect, 'getsource', lambda m: src)
    monkeypatch.setattr(tv_scan, 'AUTO_REASONS', tv_scan.AUTO_REASONS + ('unclear',))
    assert tv_scan._rule_id() != tv_scan.RERUN_RULE


def test_under_the_old_rule_nothing_was_eligible(old_rule_rows, monkeypatch, fake_tmdb):  # noqa: F811
    """What :5002 logged on ae58c3a: 'rule v161a: N review row(s), 0 eligible'."""
    monkeypatch.setattr(tv_scan, 'RERUN_RULE', 'v161a')
    out = tv_scan.auto_import_pending(now=2000.0)
    assert out['eligible'] == 0 and out['imported'] == []


def test_a_rule_bump_re_ranks_existing_review_rows(old_rule_rows, fake_tmdb, caplog):  # noqa: F811
    series_before = {r['id']: dict(r) for r in rows('SELECT * FROM series')}
    dup_before = rows("SELECT * FROM unmatched WHERE reason = 'duplicate'")
    caplog.set_level('INFO', logger='app.tv_scan')
    out = tv_scan.auto_import_pending(now=2000.0)
    assert out['total'] == 3 and out['eligible'] == 2 and out['skipped'] == 1
    assert [r['folder'] for r in rows("SELECT folder FROM series WHERE folder = 'Taskmaster'")] == ['Taskmaster']
    left = {r['folder']: r for r in rows('SELECT * FROM unmatched')}
    assert set(left) == {'Nothing Like It', 'Severance copy'}
    assert left['Nothing Like It']['ts'] == 2000.0                         # re-ranked, kept in review
    assert rows("SELECT * FROM unmatched WHERE reason = 'duplicate'") == dup_before   # duplicates skipped
    after = {r['id']: dict(r) for r in rows('SELECT * FROM series')}
    assert all(after[i] == r for i, r in series_before.items())           # imported shows never touched
    line = [r.getMessage() for r in caplog.records if 'boot re-run' in r.getMessage()][-1]
    assert tv_scan.RERUN_RULE in line and '3 review row(s), 2 eligible' in line and '1 imported' in line
    assert json.loads(tv_store.get_meta(tv_scan.RERUN_META)) == {'rule': tv_scan.RERUN_RULE, 'since': 2000.0}
    fake_tmdb.calls.clear()                                                # next boot: same rule, nothing again
    again = tv_scan.auto_import_pending(now=3000.0)
    assert again['eligible'] == 0 and not [c for c in fake_tmdb.calls if c[0] == 'search']


# ------------------------------------------------------------------ determinism (Tester b)

def _dump():
    series = sorted((r['folder'], r['tmdb_id'], r['match_tier']) for r in rows('SELECT folder, tmdb_id, match_tier FROM series'))
    review = sorted((r['folder'], r['reason'], r['tier'], r['pick'], r['candidates'])
                    for r in rows('SELECT folder, reason, tier, pick, candidates FROM unmatched'))
    return {'series': series, 'review': review}


@pytest.mark.skipif(not os.environ.get('LUMA_V166_DUMP'), reason='run by test_answers_dont_depend_on_the_hash_seed')
def test_dump_scan_answers(lib, monkeypatch, fake_tmdb):  # noqa: F811
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', True)
    tv_scan.scan_all(now=1000.0)
    names = ['The Office', 'Office', 'X-Men', 'X-Men 97', 'Life, Larry', 'Larry']
    cands = [{'id': i, 'title': n, 'original_title': n.upper(), 'aliases': [n.lower(), n + '!']} for i, n in enumerate(names)]
    scores = {q: [tv_match.title_score(q, c) for c in cands] for q in names}
    titles = [tv_match._titles(c, None) for c in cands]
    with open(os.environ['LUMA_V166_DUMP'], 'w') as f:
        json.dump({**_dump(), 'scores': scores, 'titles': titles}, f)


def test_answers_dont_depend_on_the_hash_seed(tmp_path):
    """The matcher and the scan give the same answers (and the same name lists, in the same order) under
    different PYTHONHASHSEEDs: nothing iterates a set or dict in hash order where it matters."""
    out = []
    for seed in ('0', '1', '12345'):
        dump = tmp_path / f'seed{seed}.json'
        env = {**os.environ, 'PYTHONHASHSEED': seed, 'LUMA_V166_DUMP': str(dump)}
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:randomly',
                            f'{__file__}::test_dump_scan_answers'], env=env, capture_output=True, text=True, timeout=240)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        out.append(json.loads(dump.read_text()))
    assert out[0]['series'] and out[0] == out[1] == out[2]


# ------------------------------------------------------------------ v10: the old v9's labels on :5002 (PM)

def _v9_db(rows_sql):
    """A tv.db that reached v9 on an earlier build: v9's schema, labels as that build left them."""
    import sqlite3
    p = tv_store.db_path()
    if p.exists():
        p.unlink()
    p.parent.mkdir(parents=True, exist_ok=True)
    tv_store._ready.clear()
    c = sqlite3.connect(str(p))
    for step in tv_store.MIGRATIONS[:9]:
        for stmt in tv_store._statements(step[0] if isinstance(step, tuple) else step):
            c.execute(stmt)
    for sql in rows_sql:
        c.execute(sql)
    c.execute('PRAGMA user_version = 9')
    c.commit()
    c.close()
    return p


def _state():
    s = {r['id']: (r['monitored'], r['monitor_new_seasons'], r['monitor_chosen'], r['monitor_specials'])
         for r in rows('SELECT * FROM series')}
    e = sorted((r['series_id'], r['season_number'], r['episode_number'], r['monitored']) for r in rows('SELECT * FROM episodes'))
    x = sorted((r['series_id'], r['season_number'], r['monitored']) for r in rows('SELECT * FROM seasons'))
    return s, e, x


FIVE002 = [
    # all Future (v7), switch on: never touched
    "INSERT INTO series (id, tmdb_id, title, added_at, monitored, monitor, monitor_new_seasons, monitor_chosen)"
    " VALUES (1, 1, 'Future', 0, 1, 'future', 1, 1)",
    # None, then one episode switched on by hand: the old v9 called it 'pilot'
    "INSERT INTO series (id, tmdb_id, title, added_at, monitored, monitor, monitor_chosen) VALUES (2, 2, 'Old pilot', 0, 0, 'pilot', 1)",
    # switch off, odd episodes: the old v9's closest option (computed below, whatever it is)
    "INSERT INTO series (id, tmdb_id, title, added_at, monitored, monitor, monitor_chosen) VALUES (3, 3, 'Old closest', 0, 0, 'none', 1)",
    # the owner chose Future, later switched the series off and one old episode on: not the old v9's label
    "INSERT INTO series (id, tmdb_id, title, added_at, monitored, monitor, monitor_chosen) VALUES (4, 4, 'Chosen', 0, 0, 'future', 1)",
    # switch off, nothing on, labelled: not something the old v9 touched
    "INSERT INTO series (id, tmdb_id, title, added_at, monitored, monitor, monitor_chosen) VALUES (5, 5, 'Off', 0, 0, 'future', 1)",
    'INSERT INTO seasons (series_id, season_number, monitored) VALUES (1, 1, 1), (2, 1, 1), (3, 1, 1), (3, 2, 1), (4, 1, 1), (5, 1, 0)',
    "INSERT INTO episodes (series_id, season_number, episode_number, air_date, monitored) VALUES"
    " (1, 1, 1, '2001-01-01', 0), (1, 1, 2, '2099-01-01', 1),"
    " (2, 1, 1, '2001-01-01', 1), (2, 1, 2, '2001-01-08', 0),"
    " (3, 1, 1, '2001-01-01', 0), (3, 2, 3, '2002-01-01', 1),"
    " (4, 1, 1, '2001-01-01', 1), (4, 1, 2, '2099-01-01', 1), (4, 0, 1, '2001-01-01', 0),"
    " (5, 1, 1, '2001-01-01', 0)"]


def test_v10_sets_the_old_v9_labels_back_to_none_and_changes_nothing_else(tv_env):  # noqa: F811
    import sqlite3
    p = _v9_db(FIVE002)
    c = sqlite3.connect(str(p))                         # show 3: what the old v9 gave it
    c.row_factory = sqlite3.Row
    old = tv_store._best_label(c, 3, '2026-10-18')
    c.execute('UPDATE series SET monitor = ? WHERE id = 3', (old,))
    c.commit()
    raw_before = {r['id']: dict(r) for r in c.execute('SELECT * FROM series')}
    c.close()
    assert old != 'none'
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION == 10
    got = {r['id']: r['monitor'] for r in rows('SELECT id, monitor FROM series')}
    assert got == {1: 'future', 2: 'none', 3: 'none', 4: 'future', 5: 'future'}
    after = {r['id']: dict(r) for r in rows('SELECT * FROM series')}
    for sid, r in raw_before.items():                    # labels only: every other column as it was
        assert {k: v for k, v in after[sid].items() if k != 'monitor'} == {k: v for k, v in r.items() if k != 'monitor'}


def test_v10_never_changes_monitoring_or_episode_states(tv_env):  # noqa: F811
    import sqlite3
    p = _v9_db(FIVE002)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    before = (sorted(tuple(r) for r in c.execute('SELECT id, monitored, monitor_new_seasons, monitor_chosen, monitor_specials FROM series')),
              sorted(tuple(r) for r in c.execute('SELECT series_id, season_number, episode_number, monitored FROM episodes')),
              sorted(tuple(r) for r in c.execute('SELECT series_id, season_number, monitored FROM seasons')))
    c.close()
    assert tv_store.schema_version() == 10
    s, e, x = _state()
    assert sorted((k, *v) for k, v in s.items()) == before[0] and e == before[1] and x == before[2]
    assert rows('SELECT monitor FROM series WHERE id = 1')[0]['monitor'] == 'future'      # :5002's shows stay Future


def test_v10_backs_up_first_and_is_idempotent(tv_env):  # noqa: F811
    import stat
    p = _v9_db(FIVE002)
    assert tv_store.schema_version() == 10
    bak = f'{p}.bak-v9'
    assert os.path.exists(bak) and stat.S_IMODE(os.stat(bak).st_mode) == 0o600 and tv_store._verified(bak, 9)
    import sqlite3
    b = sqlite3.connect(bak)
    assert b.execute('SELECT monitor FROM series WHERE id = 2').fetchone()[0] == 'pilot'   # the copy is v9's
    b.close()
    with tv_store.write() as conn:                       # running it again changes nothing
        assert tv_store.repair_v9_labels(conn) == 0
    tv_store._ready.clear()                              # nor does another open
    assert tv_store.schema_version() == 10 and rows("SELECT monitor FROM series WHERE id = 2")[0]['monitor'] == 'none'


def test_v10_on_a_fresh_or_already_correct_db_changes_nothing(tv_env):  # noqa: F811
    _v9_db(FIVE002[:1] + ["INSERT INTO episodes (series_id, season_number, episode_number, air_date, monitored)"
                          " VALUES (1, 1, 1, '2099-01-01', 1)"])
    assert tv_store.schema_version() == 10
    assert rows('SELECT monitor, monitored FROM series') == [{'monitor': 'future', 'monitored': 1}]
