"""TV Import routes: start and progress, review rows (tier, pick, order),
Import all (the owner's picks, surviving rescans), search by id."""
import pytest

from app import tv_scan
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def _get(c, u):
    return c.get(u, environ_base=REMOTE, headers=HDR)


def _post(c, u, body=None):
    return c.post(u, json=body, environ_base=REMOTE, headers=HDR)


@pytest.fixture
def scanned(owner, lib):  # noqa: F811
    tv_scan.run(now=1000.0)
    return owner


def test_import_starts_like_scan(owner, lib, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(tv_scan.threading, 'Thread',
                        lambda target, kwargs, **k: type('T', (), {'start': lambda self: started.append(target(**kwargs))})())
    r = _post(owner, '/api/settings/tv/import')
    assert r.status_code == 202 and r.headers['Cache-Control'] == 'no-store' and started == [True]


def test_progress_is_small_and_counts_folders(scanned):
    r = _get(scanned, '/api/settings/tv/import')
    p = r.get_json()
    assert r.headers['Cache-Control'] == 'no-store'
    assert set(p) == {'running', 'error', 'last_scan', 'total', 'done', 'matched', 'review', 'none'}
    assert p['running'] is False and p['done'] == p['total'] > 0
    assert p['matched'] + p['review'] + p['none'] == p['total']
    assert p['last_scan'] == 1000.0


def test_progress_409_while_running(owner):  # noqa: F811
    assert tv_scan._scan_lock.acquire(blocking=False)
    try:
        assert _post(owner, '/api/settings/tv/import').status_code == 409
        assert _get(owner, '/api/settings/tv/import').get_json()['running'] is True
    finally:
        tv_scan._scan_lock.release()


def test_review_rows_have_tier_pick_and_order(scanned):
    un = _get(scanned, '/api/settings/tv/library').get_json()['unmatched']
    tiers = [u['tier'] for u in un]
    assert tiers == sorted(tiers, key={'none': 0, 'duplicate': 1, 'likely': 2}.get)
    by = {u['folder']: u for u in un}
    assert by['Zzz Nothing']['tier'] == 'none' and by['Zzz Nothing']['pick'] is None
    # two shows of the same name, no files: Luma picks one (TMDB's order) and the row starts checked
    assert by['Mystery Show']['tier'] == 'likely' and by['Mystery Show']['reason'] == 'not_exact'
    assert by['Mystery Show']['pick'] in (1001, 1002)
    assert by['Mystery Show']['candidates'][0]['tmdb_id'] == by['Mystery Show']['pick']                # the pick leads


def test_import_all_imports_picks_and_they_survive_rescans(scanned):
    un = {u['folder']: u for u in _get(scanned, '/api/settings/tv/library').get_json()['unmatched']}
    r = _post(scanned, '/api/settings/tv/import-all', {'items': [{'id': un['Mystery Show']['id'], 'tmdb_id': 1002}]})
    body = r.get_json()
    assert r.status_code == 200 and body['imported'] == [un['Mystery Show']['id']] and body['failed'] == []
    show = next(s for s in body['shows'] if s['folder'] == 'Mystery Show')
    assert show['tmdb_id'] == 1002 and 'Mystery Show' not in {u['folder'] for u in body['unmatched']}
    h = rows("SELECT detail FROM history WHERE series_id = ? AND event = 'matched'", show['id'])
    assert __import__('json').loads(h[-1]['detail'])['via'] == 'manual'
    tv_scan.run(now=999999.0)                                 # a rescan (and a daily refresh) keeps the pick
    assert rows("SELECT tmdb_id FROM series WHERE folder = 'Mystery Show'")[0]['tmdb_id'] == 1002


def test_import_all_same_show_twice_goes_to_the_first(scanned, tv_env):  # noqa: F811
    (tv_env / 'TV' / 'Mystery Show (2019)').mkdir()
    tv_scan.run(now=2000.0)
    un = {u['folder']: u for u in _get(scanned, '/api/settings/tv/library').get_json()['unmatched']}
    a, b = un['Mystery Show']['id'], un['Zzz Nothing']['id']
    body = _post(scanned, '/api/settings/tv/import-all',
                 {'items': [{'id': a, 'tmdb_id': 1001}, {'id': b, 'tmdb_id': 1001}, {'id': 999999, 'tmdb_id': 1}]}
                 ).get_json()
    assert body['imported'] == [a]
    assert {f['id']: f['error'] for f in body['failed']} == {b: tv_scan.TAKEN, 999999: tv_scan.NOT_FOUND}


@pytest.mark.parametrize('body', [None, {}, {'items': []}, {'items': [{'id': 1}]}, {'items': [{'id': True, 'tmdb_id': 1}]},
                                  {'items': [{'id': 1, 'tmdb_id': -4}]}, {'items': 'x'},
                                  {'items': [{'id': 1, 'tmdb_id': 1}] * (tv_scan.IMPORT_ALL_MAX + 1)}])
def test_import_all_rejects_bad_bodies(owner, body):  # noqa: F811
    assert _post(owner, '/api/settings/tv/import-all', body).status_code == 400


def test_import_all_tmdb_down(scanned, fake_tmdb):  # noqa: F811
    un = {u['folder']: u for u in _get(scanned, '/api/settings/tv/library').get_json()['unmatched']}
    fake_tmdb.fail = "Couldn't reach TMDB"
    body = _post(scanned, '/api/settings/tv/import-all',
                 {'items': [{'id': un['Mystery Show']['id'], 'tmdb_id': 1001}]}).get_json()
    assert body['imported'] == [] and body['failed'][0]['error'] == "Couldn't reach TMDB"
    assert 'Mystery Show' in {u['folder'] for u in body['unmatched']}


@pytest.mark.parametrize('q,want', [('tmdb:95396', [95396]), ('TVDB: 371980', [95396]), ('imdb:tt11280740', [95396]),
                                    ('tmdb:nope', []), ('imdb:123', []), ('tvdb:0', [])])
def test_search_by_id(owner, fake_tmdb, q, want):  # noqa: F811
    r = owner.get('/api/settings/tv/search', query_string={'q': q}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and [x['tmdb_id'] for x in r.get_json()['results']] == want
    assert not any(c[0] == 'search' for c in fake_tmdb.calls)


def test_search_by_name_still_works(owner, fake_tmdb):  # noqa: F811
    r = owner.get('/api/settings/tv/search', query_string={'q': 'Severance'}, environ_base=REMOTE, headers=HDR)
    assert [x['tmdb_id'] for x in r.get_json()['results']] == [95396]


def test_import_routes_owner_only(minimal_app, lib):  # noqa: F811
    _client(minimal_app)
    other = _client(minimal_app, name='second')
    for m, u in (('get', '/api/settings/tv/import'), ('post', '/api/settings/tv/import'),
                 ('post', '/api/settings/tv/import-all')):
        assert getattr(other, m)(u, environ_base=REMOTE, headers=HDR).status_code == 403
        assert getattr(minimal_app.test_client(), m)(u, environ_base=REMOTE, headers=HDR).status_code == 401


def test_import_all_never_takes_a_show_another_folder_has(scanned):
    """Tester on 4d22f6c: Import all moved The Office (US) and SmackDown off their folders. It never
    assigns a show another folder has; the row becomes a duplicate in review (unchecked) instead."""
    sev = rows("SELECT id, folder, path_id FROM series WHERE tmdb_id = 95396")[0]
    un = {u['folder']: u for u in _get(scanned, '/api/settings/tv/library').get_json()['unmatched']}
    a = un['Mystery Show']['id']
    before = rows('SELECT COUNT(*) AS n FROM history')[0]['n']
    body = _post(scanned, '/api/settings/tv/import-all', {'items': [{'id': a, 'tmdb_id': 95396}]}).get_json()
    assert body['imported'] == [] and body['failed'] == [{'id': a, 'error': tv_scan.TAKEN}]
    assert rows("SELECT id, folder, path_id FROM series WHERE tmdb_id = 95396")[0] == sev       # stays where it is
    dup = next(u for u in body['unmatched'] if u['folder'] == 'Mystery Show')
    assert (dup['tier'], dup['pick'], dup['reason']) == ('duplicate', 95396, 'duplicate')
    assert [c['tmdb_id'] for c in dup['candidates']] == [95396]
    assert rows('SELECT COUNT(*) AS n FROM history')[0]['n'] == before                         # nothing moved
    tv_scan.run(now=999999.0)                                  # a rescan doesn't move it either
    assert rows("SELECT id, folder FROM series WHERE tmdb_id = 95396")[0]['folder'] == sev['folder']


def test_shows_carry_series_grid_progress(scanned):
    from app import tv_store
    shows = {s['title']: s for s in _get(scanned, '/api/settings/tv/library').get_json()['shows']}
    s = next(iter(shows.values()))
    assert {'aired', 'have', 'status', 'continuing', 'added_at'} <= set(s)
    for s in shows.values():
        assert 0 <= s['have'] <= s['aired'] and isinstance(s['continuing'], bool)
    with tv_store.connect() as conn:
        sid = shows['Severance']['id']
        aired = conn.execute("SELECT COUNT(*) AS n FROM episodes WHERE series_id = ? AND season_number > 0 AND air_date <= date('now')",
                             (sid,)).fetchone()['n']
    assert abs(shows['Severance']['aired'] - aired) <= 1          # aired, not specials (local vs UTC day)


@pytest.fixture(autouse=True)
def _review_rows_stay(monkeypatch):
    """These tests drive the review flows (or count review rows): keep the pre-v157 rule so the small tree
    still has same-name rows in review. v157's auto-import is covered in test_tv_scan and the 687 fixture."""
    from app import tv_scan as _ts
    monkeypatch.setattr(_ts, 'AUTO_IMPORT', False)
