"""Phase 1 scan: shows, unmatched with candidates, file states, monitored (v158a: Future),
history, rows only for gone folders, Scan and library routes."""
import json
import os
import time

import pytest

from app import file_management as fm, tv_scan, tv_settings, tv_store, tv_tmdb
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.tv_tree import build

PID = 'p_0000aaaa'


def select(pid, root, name='TV'):
    """Make *root* a Library path *pid* and select it as a TV library."""
    data = fm.load()
    data['paths'] = [p for p in data['paths'] if p['id'] != pid] + [{'id': pid, 'name': name, 'path': str(root)}]
    fm.save(data)
    st = tv_settings.load()
    st['libraries'] = [i for i in st['libraries'] if i != pid] + [pid]
    tv_settings.save(st)


@pytest.fixture
def lib(tv_env, fake_tmdb, tmp_path):  # noqa: F811
    root = build(tv_env / 'TV', outside=tmp_path / 'outside')
    select(PID, root)
    tv_scan._state.update(running=False, error=None, summary=None)
    tv_scan._backoff.update(fails=0, until=0.0)
    return root


@pytest.fixture
def review_rule(monkeypatch):
    """The review flows (rows, picks, Import all) on the small tree: the pre-v157 rule leaves Mystery
    Show in review. The v157 auto-import rule is covered in test_scan_matches_and_records and the 687."""
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', False)


def rows(sql, *a):
    with tv_store.connect() as conn:
        return [dict(r) for r in conn.execute(sql, a)]


def test_scan_matches_and_records(lib):
    out = tv_scan.scan_all(now=1000.0)
    # v157: a folder with any TMDB result imports Luma's ranked pick (Mystery Show: TMDB's order breaks the
    # same-name tie); only no-result folders (and duplicates) stay in review
    assert out['paths'] == 1 and out['shows'] == 6 and out['unmatched'] == 2
    shows = {r['folder']: r for r in rows('SELECT * FROM series')}
    assert {f: s['tmdb_id'] for f, s in shows.items()} == {
        'Severance (2022)': 95396, 'The Office (US) {tvdb-73244}': 2316, 'Dark [tmdbid-70523]': 70523,
        'Taskmaster [imdbid-tt4934214]': 63770, 'The Daily Show': 4000, 'Mystery Show': 1001}
    assert shows['Mystery Show']['match_tier'] == 'auto'
    assert 'Escaped (2010)' not in shows and '.hidden' not in shows and 'readme.txt' not in shows
    # v158a: a library folder imports Monitored with Future episodes and new seasons on
    assert all(s['monitored'] == 1 and s['monitor_new_seasons'] == 1 and s['monitor'] == 'future' for s in shows.values())
    assert shows['Severance (2022)']['tvdb_id'] == 371980 and shows['Severance (2022)']['imdb_id'] == 'tt11280740'
    un = {r['folder']: r for r in rows('SELECT * FROM unmatched')}
    assert set(un) == {'Zzz Nothing', 'Empty Show (2020)'}
    assert un['Zzz Nothing']['reason'] == 'no_results'
    assert tv_scan.last_scan() == 1000.0


def test_file_states(lib):
    tv_scan.scan_all(now=1000.0)
    f = {r['rel_path']: r for r in rows('SELECT * FROM episode_files')}
    S = 'Severance (2022)/'
    assert f[S + 'Season 01/Severance - S01E01 - Good News About Hell.mkv']['state'] == 'linked'
    assert f[S + 'Season 01/Severance.S01E02.1080p.WEB-DL-GRP.mkv']['state'] == 'linked'
    assert f[S + 'Season 01/Severance.S01E02.1080p.WEB-DL-GRP.mkv']['resolution'] == '1080p'
    assert f[S + 'Season 01/Severance.S02E05.mkv']['state'] == 'linked'          # its own S/E wins over the folder
    assert f[S + 'Season 02/Severance.S02E01E02.2160p.mkv']['state'] == 'linked'
    assert f[S + 'Season 02/Severance.S02E40.mkv']['state'] == 'mismatch'
    assert f[S + 'Extras/Behind the scenes.mkv']['state'] == 'unrecognized'
    assert S + 'Season 01/folder.jpg' not in f and S + 'Season 01/Severance.S01E01.nfo' not in f
    assert f['The Office (US) {tvdb-73244}/Season 2/05.mkv']['state'] == 'linked'
    assert f['The Daily Show/The.Daily.Show.2024.10.03.mkv']['state'] == 'linked'
    assert 'Dark [tmdbid-70523]/link.S01E02.mkv' not in f            # symlink out of the library
    assert all(r['origin'] == 'scan' for r in f.values())
    multi = f[S + 'Season 02/Severance.S02E01E02.2160p.mkv']
    eps = rows('SELECT e.season_number, e.episode_number FROM episode_file_links l JOIN episodes e'
               ' ON e.id = l.episode_id WHERE l.file_id = ?', multi['id'])
    assert sorted((e['season_number'], e['episode_number']) for e in eps) == [(2, 1), (2, 2)]
    assert json.loads(multi['parsed'])['summary'] == 'multi S02E01E02'
    st = os.stat(lib / 'Severance (2022)' / 'Season 02' / 'Severance.S02E01E02.2160p.mkv')
    assert (multi['size'], multi['ino']) == (st.st_size, st.st_ino)
    import datetime as _dt
    today = _dt.date.today().isoformat()
    assert rows('SELECT COUNT(*) AS n FROM episodes WHERE monitored = 1 AND air_date < ?', today)[0]['n'] == 0   # Future only


def test_history_records_matches(lib):
    tv_scan.scan_all(now=1000.0)
    h = rows("SELECT event, detail FROM history WHERE event = 'matched'")
    vias = sorted(json.loads(r['detail'])['via'] for r in h)
    assert vias == ['auto', 'imdb', 'search', 'search', 'tmdb', 'tvdb']


def test_rescan_is_stable_and_drops_gone_rows_only(lib):
    tv_scan.scan_all(now=1000.0)
    ids = {r['folder']: r['id'] for r in rows('SELECT id, folder FROM series')}
    n_files = len(rows('SELECT id FROM episode_files'))
    tv_scan.scan_all(now=2000.0)
    assert {r['folder']: r['id'] for r in rows('SELECT id, folder FROM series')} == ids
    assert len(rows('SELECT id FROM episode_files')) == n_files
    os.rename(lib / 'Dark [tmdbid-70523]', lib.parent / 'Dark-moved-away')      # the user moved it
    (lib / 'Severance (2022)' / 'Season 02' / 'Severance.S02E40.mkv').unlink()
    tv_scan.scan_all(now=3000.0)
    folders = {r['folder'] for r in rows('SELECT folder FROM series')}
    assert 'Dark [tmdbid-70523]' not in folders and 'Severance (2022)' in folders
    assert not any(r['rel_path'].endswith('S02E40.mkv') for r in rows('SELECT rel_path FROM episode_files'))


def test_missing_library_path_keeps_its_rows(lib):
    tv_scan.scan_all(now=1000.0)
    before = len(rows('SELECT id FROM series'))
    os.rename(lib, lib.parent / 'TV-unmounted')
    out = tv_scan.scan_all(now=2000.0)
    assert out['missing'] == [PID] and len(rows('SELECT id FROM series')) == before


def test_scheduled_style_scan_keeps_unmatched_without_asking_tmdb(review_rule, lib, fake_tmdb):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    fake_tmdb.calls.clear()
    tv_scan.scan_all(now=2000.0, retry_unmatched=False)
    assert not any(c[0] == 'search' for c in fake_tmdb.calls)
    assert len(rows('SELECT id FROM unmatched')) == 3


def test_same_show_in_two_folders(lib):
    tv_scan.scan_all(now=1000.0)
    (lib / 'Severance').mkdir()                      # added later: the first folder keeps the show
    tv_scan.scan_all(now=2000.0)
    un = {r['folder']: r for r in rows('SELECT * FROM unmatched')}
    assert un['Severance']['reason'] == 'duplicate'
    assert [c['tmdb_id'] for c in json.loads(un['Severance']['candidates'])] == [95396]
    assert rows("SELECT folder FROM series WHERE tmdb_id = 95396") == [{'folder': 'Severance (2022)'}]


def test_no_tmdb_key_stops_with_a_clear_error(lib, fake_tmdb):  # noqa: F811
    fake_tmdb.fail = tv_tmdb.NO_KEY
    assert tv_scan.run() is True
    assert tv_scan.status()['error'] == 'Add a TMDB key in Connectors'
    assert rows('SELECT id FROM series') == [] and rows('SELECT id FROM unmatched') == []


def test_one_scan_at_a_time(lib):
    assert tv_scan._scan_lock.acquire(blocking=False)
    try:
        assert tv_scan.run() is False and tv_scan.start() is False
        assert tv_scan.status()['running'] is True
    finally:
        tv_scan._scan_lock.release()


# ------------------------------------------------------------------ routes

@pytest.fixture
def owner(minimal_app, lib):
    return _client(minimal_app)


def test_scan_route_and_library_view(review_rule, owner, lib, monkeypatch):
    started = []
    monkeypatch.setattr(tv_scan.threading, 'Thread',
                        lambda target, kwargs, **k: type('T', (), {'start': lambda self: started.append(target(**kwargs))})())
    r = owner.post('/api/settings/tv/scan', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 202 and r.headers['Cache-Control'] == 'no-store' and started == [True]
    body = owner.get('/api/settings/tv/library', environ_base=REMOTE, headers=HDR).get_json()
    sev = next(s for s in body['shows'] if s['folder'] == 'Severance (2022)')
    assert (sev['title'], sev['year'], sev['path_name'], sev['monitored']) == ('Severance', 2022, 'TV', True)
    assert (sev['files'], sev['linked'], sev['mismatch'], sev['unrecognized']) == (6, 4, 1, 1)
    assert sev['episodes'] == 5            # S01E01, S01E02, S02E05 (its own S/E wins), S02E01E02 (one file, two)
    assert {u['folder'] for u in body['unmatched']} == {'Mystery Show', 'Zzz Nothing', 'Empty Show (2020)'}
    assert body['scan']['running'] is False and body['scan']['error'] is None and body['scan']['last_scan']


def test_scan_route_409_while_running(owner):
    assert tv_scan._scan_lock.acquire(blocking=False)
    try:
        r = owner.post('/api/settings/tv/scan', environ_base=REMOTE, headers=HDR)
        assert r.status_code == 409 and r.get_json()['error'] == 'A scan is already running'
    finally:
        tv_scan._scan_lock.release()


def test_routes_owner_only(minimal_app, lib):
    _client(minimal_app)
    other = _client(minimal_app, name='second')
    for m, u in (('get', '/api/settings/tv/library'), ('post', '/api/settings/tv/scan')):
        assert getattr(other, m)(u, environ_base=REMOTE, headers=HDR).status_code == 403
        assert getattr(minimal_app.test_client(), m)(u, environ_base=REMOTE, headers=HDR).status_code == 401


def test_auto_import_never_takes_a_show_another_folder_has(lib):
    """v157: the second folder whose ranked pick is already owned stays in review as a duplicate."""
    (lib / 'Mystery  Show').mkdir()
    tv_scan.scan_all(now=1000.0)
    owners = [r['folder'] for r in rows('SELECT folder FROM series WHERE tmdb_id = 1001')]
    assert len(owners) == 1
    dup = {r['folder']: r for r in rows('SELECT * FROM unmatched')}
    other = ({'Mystery Show', 'Mystery  Show'} - set(owners)).pop()
    assert dup[other]['reason'] == 'duplicate' and dup[other]['tier'] == 'duplicate'


def test_auto_pick_is_lumas_ranked_pick_and_unclear_stays():
    m = {'status': 'unmatched', 'reason': 'not_exact', 'pick': 7, 'candidates': [{'tmdb_id': 9}, {'tmdb_id': 7}],
         'details': None, 'tier': 'likely'}
    assert tv_scan.auto_pick(m)['tmdb_id'] == 7 and tv_scan.auto_pick(m)['tier'] == 'auto'   # never TMDB's first (9)
    assert tv_scan.auto_pick({**m, 'reason': 'unclear'})['status'] == 'unmatched'
    assert tv_scan.auto_pick({**m, 'pick': None})['status'] == 'unmatched'
    assert tv_scan.auto_pick({**m, 'reason': 'no_results', 'pick': None})['status'] == 'unmatched'


def test_boot_rerun_imports_old_pending_rows_and_touches_nothing_else(lib, monkeypatch, fake_tmdb):
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', False)                 # rows left by the old rule
    tv_scan.scan_all(now=1000.0)
    before = {r['id']: dict(r) for r in rows('SELECT * FROM series')}
    assert 'Mystery Show' in {r['folder'] for r in rows('SELECT folder FROM unmatched')}
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', True)
    fake_tmdb.calls.clear()
    out = tv_scan.auto_import_pending(now=2000.0)
    assert out['ok'] and len(out['imported']) == 1 and out['failed'] == [] and out['kept'] == 2
    s = rows("SELECT tmdb_id, match_tier FROM series WHERE folder = 'Mystery Show'")[0]
    assert s['tmdb_id'] == 1001
    assert not [c for c in fake_tmdb.calls if c[0] == 'search']                    # no rescan, no new guesses
    after = {r['id']: dict(r) for r in rows('SELECT * FROM series')}
    assert all(after[i] == r for i, r in before.items())                          # imported shows untouched
    assert {r['folder'] for r in rows('SELECT folder FROM unmatched')} == {'Zzz Nothing', 'Empty Show (2020)'}
    assert tv_scan.auto_import_pending(now=3000.0)['imported'] == []              # idempotent


def test_v158a_boot_rerun_fetches_and_walks_without_the_scan_lock(lib, monkeypatch, fake_tmdb):
    """Reviewer on v158a: auto_import_pending takes _scan_lock only around the writes."""
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', False)
    tv_scan.scan_all(now=1000.0)
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', True)
    held = {'fetch': [], 'walk': [], 'store': []}
    real_fetch, real_list, real_store = tv_scan.fetch_show, tv_scan.list_files, tv_scan.store_show
    monkeypatch.setattr(tv_scan, 'fetch_show', lambda *a, **k: (held['fetch'].append(tv_scan._scan_lock.locked()), real_fetch(*a, **k))[1])
    monkeypatch.setattr(tv_scan, 'list_files', lambda *a: (held['walk'].append(tv_scan._scan_lock.locked()), real_list(*a))[1])
    monkeypatch.setattr(tv_scan, 'store_show', lambda *a, **k: (held['store'].append(tv_scan._scan_lock.locked()), real_store(*a, **k))[1])
    out = tv_scan.auto_import_pending(now=2000.0)
    assert out['ok'] and len(out['imported']) == 1
    assert held == {'fetch': [False], 'walk': [False], 'store': [True]}
    assert not tv_scan._scan_lock.locked()


def test_v158a_boot_rerun_waits_for_a_running_scan_then_skips_rows_it_changed(lib, monkeypatch, fake_tmdb):
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', False)
    tv_scan.scan_all(now=1000.0)
    monkeypatch.setattr(tv_scan, 'AUTO_IMPORT', True)
    monkeypatch.setattr(tv_scan, 'LOCK_WAIT_S', 0.05)
    with tv_scan._scan_lock:
        out = tv_scan.auto_import_pending(now=2000.0)
    assert out['ok'] is False and out['error'] == tv_scan.BUSY and out['imported'] == []
    real_fetch = tv_scan.fetch_show

    def fetch_then_gone(*a, **k):                    # a scan drops the row while the show is fetched
        with tv_store.write() as conn:
            conn.execute("DELETE FROM unmatched WHERE folder = 'Mystery Show'")
        return real_fetch(*a, **k)
    monkeypatch.setattr(tv_scan, 'fetch_show', fetch_then_gone)
    out = tv_scan.auto_import_pending(now=2000.0)
    assert out['ok'] and out['imported'] == [] and out['failed'] == []
    assert not rows("SELECT 1 FROM series WHERE folder = 'Mystery Show'")
