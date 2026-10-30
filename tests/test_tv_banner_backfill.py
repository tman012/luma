"""The series banner backfill (tv_banners): shows with a tvdb_id and no banner_url are asked TheTVDB for the
banner only, paced and capped per pass, idempotent, backed off on failure, and a show with none is asked again
after RECHECK_S. Migration v14 adds banner_checked_at. Adds and scans poke the backfill."""
import logging
import sqlite3

import pytest
import requests

from app import partial_cleanup as pc, tv_banners, tv_store, tv_tvdb
from tests.test_tv_calendar import _post, aired, scanned  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_file_management import store, minimal as minimal_app  # noqa: F401
from tests.test_tv_foundations import db  # noqa: F401

ART = 'https://artworks.thetvdb.com/banners/graphical/{}-g1.jpg'
NOW = 10 ** 9


class Clock:
    def __init__(self):
        self.t, self.sleeps = 100.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def bf(scanned, monkeypatch):  # noqa: F811
    """Every scanned show gets a distinct tvdb_id and no banner; a TheTVDB key is saved; banner() is faked."""
    with tv_store.write() as conn:
        for i, r in enumerate(conn.execute('SELECT id FROM series').fetchall()):
            conn.execute('UPDATE series SET tvdb_id = ?, banner_url = NULL, banner_checked_at = NULL WHERE id = ?',
                         (900 + i, r['id']))
    monkeypatch.setattr('app.connectors.tvdb_credentials', lambda: ('k', ''))
    calls, arts = [], {}

    def banner(tvdb_id, creds):
        calls.append(tvdb_id)
        v = arts.get(tvdb_id, 'default')
        if isinstance(v, Exception):
            raise v
        return ART.format(tvdb_id) if v == 'default' else v
    monkeypatch.setattr(tv_tvdb, 'banner', banner)
    clock = Clock()
    tv_banners.configure(sleep=clock.sleep, clock=clock)
    yield {'calls': calls, 'arts': arts, 'clock': clock}
    tv_banners.configure()


def _series():
    with tv_store.connect() as conn:
        return {r['tvdb_id']: dict(r) for r in conn.execute('SELECT id, tmdb_id, tvdb_id, banner_url, banner_checked_at FROM series')}


def test_schema_v14_adds_banner_checked_at(db):  # noqa: F811
    assert tv_store.schema_version() == tv_store.SCHEMA_VERSION == 14
    with tv_store.connect() as conn:
        assert 'banner_checked_at' in {r[1] for r in conn.execute('PRAGMA table_info(series)')}


def test_a_v13_file_migrates_to_v14_keeping_banners(db):  # noqa: F811
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db))
    for step in tv_store.MIGRATIONS[:13]:
        sql = step[0] if isinstance(step, tuple) else step
        for stmt in tv_store._statements(sql):
            conn.execute(stmt)
    conn.execute("INSERT INTO series (tmdb_id, title, added_at, path_id, folder, banner_url)"
                 " VALUES (1, 'A', 0, 'tp_00000001', 'A', 'https://artworks.thetvdb.com/banners/a.jpg')")
    conn.execute('PRAGMA user_version = 13')
    conn.commit()
    conn.close()
    assert tv_store.schema_version() == 14
    with tv_store.connect() as c:
        r = c.execute('SELECT banner_url, banner_checked_at FROM series').fetchone()
    assert (r['banner_url'], r['banner_checked_at']) == ('https://artworks.thetvdb.com/banners/a.jpg', None)
    assert (db.parent / 'tv.db.bak-v13').exists()


def test_backfill_fills_null_rows_and_logs_one_summary(bf, caplog):
    caplog.set_level(logging.INFO, logger='app.tv_banners')
    out = tv_banners.run_pass(now=NOW)
    s = _series()
    assert out['asked'] == out['found'] == len(s) > 0
    assert all(v['banner_url'] == ART.format(k) and v['banner_checked_at'] == NOW for k, v in s.items())
    msgs = [r.message for r in caplog.records if r.name == 'app.tv_banners']
    assert len(msgs) == 1 and msgs[0].startswith('Series banners: asked')


def test_backfill_skips_without_a_key(bf, monkeypatch):
    monkeypatch.setattr('app.connectors.tvdb_credentials', lambda: ('', ''))
    assert tv_banners.run_pass(now=NOW)['asked'] == 0
    assert bf['calls'] == []


def test_backfill_is_idempotent_and_keeps_existing_banners(bf):
    first = sorted(_series())[0]
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET banner_url = 'https://artworks.thetvdb.com/keep.jpg' WHERE tvdb_id = ?", (first,))
    tv_banners.run_pass(now=NOW)
    assert first not in bf['calls']
    n = len(bf['calls'])
    assert tv_banners.run_pass(now=NOW + 60)['asked'] == 0 and len(bf['calls']) == n
    assert _series()[first]['banner_url'] == 'https://artworks.thetvdb.com/keep.jpg'


def test_cap_and_pacing(bf, monkeypatch):
    monkeypatch.setattr(tv_banners, 'MAX_PER_PASS', 1)
    total = len(_series())
    assert total >= 2
    assert tv_banners.run_pass(now=NOW)['asked'] == 1
    assert tv_banners.run_pass(now=NOW + 1)['asked'] == 1
    assert len(bf['calls']) == 2
    # the second call waited PACE_S after the first (fake clock never advanced on its own)
    assert bf['clock'].sleeps == [pytest.approx(tv_banners.PACE_S)]


def test_pacing_spaces_every_call(bf):
    tv_banners.run_pass(now=NOW)
    n = len(bf['calls'])
    assert bf['clock'].sleeps == [pytest.approx(tv_banners.PACE_S)] * (n - 1)


def test_failure_backs_off_then_recovers(bf):
    ids = sorted(_series(), key=lambda k: _series()[k]['id'])          # asked in series id order
    bf['arts'][ids[0]] = requests.ConnectionError('down')
    out = tv_banners.run_pass(now=NOW)
    assert out['asked'] == 0 and bf['calls'] == [ids[0]]
    assert tv_banners.run_pass(now=NOW + 30)['asked'] == 0 and len(bf['calls']) == 1   # backing off (1 min)
    assert tv_banners._fail['until'] == NOW + tv_banners.BACKOFF_S
    bf['arts'].pop(ids[0])
    assert tv_banners.run_pass(now=NOW + tv_banners.BACKOFF_S)['found'] == len(ids)
    assert tv_banners._fail['n'] == 0


def test_backoff_doubles_and_caps(bf):
    ids = sorted(_series(), key=lambda k: _series()[k]['id'])
    bf['arts'][ids[0]] = tv_tvdb.TvdbError('x')
    t = NOW
    waits = []
    for _ in range(9):
        tv_banners.run_pass(now=t)
        waits.append(tv_banners._fail['until'] - t)
        t = tv_banners._fail['until']
    assert waits[:3] == [60.0, 120.0, 240.0] and waits[-1] == tv_banners.BACKOFF_MAX_S


def test_write_guards_on_tmdb_id(bf, monkeypatch):
    ids = sorted(_series())
    real = tv_tvdb.banner

    def rematch(tvdb_id, creds):            # the show is rematched mid-pass
        if tvdb_id == ids[0]:
            with tv_store.write() as conn:
                conn.execute('UPDATE series SET tmdb_id = 424242 WHERE tvdb_id = ?', (tvdb_id,))
        return real(tvdb_id, creds)
    monkeypatch.setattr(tv_tvdb, 'banner', rematch)
    out = tv_banners.run_pass(now=NOW)
    assert out['skipped'] == 1
    r = _series()[ids[0]]
    assert (r['banner_url'], r['banner_checked_at']) == (None, None)


def test_none_found_is_rechecked_only_after_the_window(bf):
    ids = sorted(_series())
    bf['arts'][ids[0]] = None
    assert tv_banners.run_pass(now=NOW)['none'] == 1
    assert _series()[ids[0]]['banner_checked_at'] == NOW
    bf['calls'].clear()
    tv_banners.run_pass(now=NOW + tv_banners.RECHECK_S - 1)
    assert bf['calls'] == []
    bf['arts'][ids[0]] = 'default'
    assert tv_banners.run_pass(now=NOW + tv_banners.RECHECK_S)['found'] == 1
    assert bf['calls'] == [ids[0]] and _series()[ids[0]]['banner_url'] == ART.format(ids[0])


def test_key_change_asks_none_found_shows_again(bf):
    ids = sorted(_series())
    bf['arts'][ids[0]] = None
    tv_banners.run_pass(now=NOW)
    tv_banners.forget()
    assert _series()[ids[0]]['banner_checked_at'] is None
    assert all(_series()[i]['banner_url'] for i in ids[1:])       # found banners stay


def test_a_new_show_pokes_the_backfill(scanned, owner, tv_env, monkeypatch):  # noqa: F811
    pokes = []
    monkeypatch.setattr(tv_banners, 'poke', lambda: pokes.append(1))
    r = _post(owner, '/api/settings/tv/shows', {'tmdb_id': 6001})
    assert r.status_code == 201 and pokes


def test_a_scan_refresh_pokes_the_backfill(monkeypatch):
    from app import tv_airtimes, tv_scan
    pokes = []
    monkeypatch.setattr(tv_banners, 'poke', lambda: pokes.append(1))
    monkeypatch.setattr(tv_scan, 'refresh_added', lambda **k: None)
    monkeypatch.setattr(tv_airtimes, 'refresh', lambda **k: 0)
    tv_scan._refresh_pass(None, set(), set())
    assert pokes == [1]


def test_partial_sweep_logs_its_startup_pass(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger='app.partial_cleanup')
    monkeypatch.setattr(pc, '_last', {'t': 0.0})
    monkeypatch.setattr(pc, 'library_roots', lambda: [str(tmp_path), str(tmp_path / 'b')])
    monkeypatch.setattr(pc, 'sweep', lambda roots: [])
    pc.maybe_sweep(now=1000.0)
    pc.maybe_sweep(now=1000.0 + pc.SWEEP_EVERY_S)
    recs = [r for r in caplog.records if r.name == 'app.partial_cleanup']
    assert [(r.levelno, r.getMessage()) for r in recs] == [
        (logging.INFO, 'Partial cleanup (startup): swept 2 roots, removed 0'),
        (logging.DEBUG, 'Partial cleanup: swept 2 roots, removed 0')]


def test_both_series_routes_return_banner_url(bf, owner):  # noqa: F811
    from tests.test_tv_calendar import _get
    tv_banners.run_pass(now=NOW)
    sid, tvdb = next((v['id'], k) for k, v in _series().items())
    for u in (f'/api/settings/tv/shows/{sid}', f'/api/settings/tv/shows/{sid}/detail'):
        r = _get(owner, u)
        assert r.status_code == 200, u
        assert r.get_json()['banner_url'] == ART.format(tvdb), u
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET banner_url = NULL WHERE id = ?', (sid,))
    body = _get(owner, f'/api/settings/tv/shows/{sid}').get_json()
    assert 'banner_url' in body and body['banner_url'] is None
