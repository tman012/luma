"""Fix-match: "Wrong show?" (rematched) and "Match" for unmatched folders
(matched, via manual), the picker search, and history."""
import json

import pytest

from app import tv_scan, tv_store, tv_tmdb
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_library_paths import tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.tv_tree import snapshot


def _sid(folder):
    return rows('SELECT id FROM series WHERE folder = ?', folder)[0]['id']


def _uid(folder):
    return rows('SELECT id FROM unmatched WHERE folder = ?', folder)[0]['id']


@pytest.fixture
def scanned(lib):  # noqa: F811
    tv_scan.scan_all(now=1000.0)
    return lib


def test_rematch_repoints_and_relinks(scanned):
    sid = _sid('Severance (2022)')
    out = tv_scan.rematch(sid, 1001, now=2000.0)
    assert out == {'ok': True, 'series_id': sid}
    s = rows('SELECT * FROM series WHERE id = ?', sid)[0]
    assert (s['tmdb_id'], s['title'], s['folder'], s['monitored']) == (1001, 'Mystery Show', 'Severance (2022)', 0)
    f = {r['rel_path']: r['state'] for r in rows('SELECT rel_path, state FROM episode_files WHERE series_id = ?', sid)}
    assert f['Severance (2022)/Season 01/Severance.S01E02.1080p.WEB-DL-GRP.mkv'] == 'linked'
    assert f['Severance (2022)/Season 02/Severance.S02E01E02.2160p.mkv'] == 'mismatch'  # it has no season 2
    h = rows("SELECT detail FROM history WHERE event = 'rematched'")
    assert len(h) == 1 and json.loads(h[0]['detail']) == {
        'via': 'manual', 'folder': 'Severance (2022)', 'path_id': 't_0000aaaa', 'tmdb_id': 1001,
        'title': 'Mystery Show', 'from_tmdb_id': 95396, 'from_title': 'Severance'}


def test_rematch_to_a_show_owned_by_another_folder(scanned):
    # Tester tv1-8646479 M2: no 409 dead end. The pick moves Dark here; Dark's
    # folder becomes the unmatched duplicate, its candidate Dark itself.
    dark = _sid('Dark [tmdbid-70523]')
    out = tv_scan.rematch(_sid('Severance (2022)'), 70523, now=2000.0)
    assert out == {'ok': True, 'series_id': dark}
    assert rows('SELECT id, tmdb_id FROM series WHERE folder = ?', 'Severance (2022)') == [{'id': dark, 'tmdb_id': 70523}]
    assert rows('SELECT id FROM series WHERE tmdb_id = 95396') == []
    un = rows('SELECT * FROM unmatched WHERE folder = ?', 'Dark [tmdbid-70523]')[0]
    assert un['reason'] == 'duplicate' and [(c['tmdb_id'], c['title']) for c in json.loads(un['candidates'])] == [(70523, 'Dark')]
    h = json.loads(rows("SELECT detail FROM history WHERE event = 'rematched' AND series_id = ?", dark)[-1]['detail'])
    assert (h['from_folder'], h['from_tmdb_id'], h['folder']) == ('Dark [tmdbid-70523]', 95396, 'Severance (2022)')


def test_rematch_unknown(scanned):
    assert tv_scan.rematch(99999, 1001)['status'] == 404
    assert tv_scan.rematch(_sid('Dark [tmdbid-70523]'), 424242)['error'] == tv_scan.SHOW_NOT_FOUND


def test_match_unmatched(scanned):
    uid = _uid('Mystery Show')
    out = tv_scan.match_unmatched(uid, 1002, now=2000.0)
    assert out['ok']
    assert rows('SELECT id FROM unmatched WHERE id = ?', uid) == []
    s = rows('SELECT * FROM series WHERE id = ?', out['series_id'])[0]
    assert (s['folder'], s['tmdb_id'], s['monitored']) == ('Mystery Show', 1002, 0)
    assert rows('SELECT state FROM episode_files WHERE series_id = ?', s['id']) == [{'state': 'linked'}]
    h = [json.loads(r['detail']) for r in rows("SELECT detail FROM history WHERE event = 'matched'"
                                               ' AND series_id = ?', s['id'])]
    assert h == [{'via': 'manual', 'folder': 'Mystery Show', 'path_id': 't_0000aaaa', 'tmdb_id': 1002,
                  'title': 'Mystery Show'}]
    tv_scan.scan_all(now=3000.0)                                      # a rescan keeps the manual match
    assert rows('SELECT tmdb_id FROM series WHERE folder = ?', 'Mystery Show') == [{'tmdb_id': 1002}]


def test_fix_match_touches_no_files(scanned):
    before = snapshot(scanned)
    assert tv_scan.rematch(_sid('Severance (2022)'), 5000, now=2000.0)['ok']
    assert tv_scan.match_unmatched(_uid('Mystery Show'), 1001, now=2000.0)['ok']
    assert snapshot(scanned) == before


def test_waits_for_a_running_scan(scanned, monkeypatch):
    monkeypatch.setattr(tv_scan, 'LOCK_WAIT_S', 0.01)
    assert tv_scan._scan_lock.acquire(blocking=False)
    try:
        out = tv_scan.match_unmatched(_uid('Mystery Show'), 1001)
        assert out == {'ok': False, 'error': tv_scan.BUSY, 'status': 409}
    finally:
        tv_scan._scan_lock.release()


# ------------------------------------------------------------------ routes

@pytest.fixture
def owner(minimal_app, scanned):
    return _client(minimal_app)


def test_search_route(owner, fake_tmdb):  # noqa: F811
    r = owner.get('/api/settings/tv/search?q=mystery&year=2019', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and [x['tmdb_id'] for x in r.get_json()['results']] == [1002]
    assert ('search', 'mystery', 2019) in fake_tmdb.calls
    assert owner.get('/api/settings/tv/search?q=mystery&year=abc', environ_base=REMOTE,
                     headers=HDR).status_code == 200
    for q in ('', '%20', 'x' * 201):
        r = owner.get(f'/api/settings/tv/search?q={q}', environ_base=REMOTE, headers=HDR)
        assert r.status_code == 400 and r.get_json()['error'] == 'Enter a show name'
    fake_tmdb.fail = tv_tmdb.UNREACHABLE
    r = owner.get('/api/settings/tv/search?q=x', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 502 and r.get_json()['error'] == "Couldn't reach TMDB"


def test_match_routes(owner):
    uid, sid = _uid('Mystery Show'), _sid('Severance (2022)')
    r = owner.post(f'/api/settings/tv/unmatched/{uid}/match', json={'tmdb_id': 1001}, environ_base=REMOTE,
                   headers=HDR)
    assert r.status_code == 200 and r.get_json()['ok'] is True
    assert 'Mystery Show' not in {u['folder'] for u in r.get_json()['unmatched']}
    r = owner.put(f'/api/settings/tv/shows/{sid}/match', json={'tmdb_id': 5000}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    assert next(s for s in r.get_json()['shows'] if s['id'] == sid)['title'] == 'Bluey'
    r = owner.put(f'/api/settings/tv/shows/{sid}/match', json={'tmdb_id': 70523}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and 'Dark [tmdbid-70523]' in {u['folder'] for u in r.get_json()['unmatched']}
    sid = next(s['id'] for s in r.get_json()['shows'] if s['folder'] == 'Severance (2022)')
    for bad in ({}, {'tmdb_id': '5'}, {'tmdb_id': True}, {'tmdb_id': -1}, [1], 'x'):
        r = owner.put(f'/api/settings/tv/shows/{sid}/match', json=bad, environ_base=REMOTE, headers=HDR)
        assert r.status_code == 400 and r.get_json()['error'] == 'Pick a show'
    assert owner.post('/api/settings/tv/unmatched/9999/match', json={'tmdb_id': 1}, environ_base=REMOTE,
                      headers=HDR).status_code == 404


def test_match_routes_owner_only(minimal_app, scanned):
    _client(minimal_app)
    other = _client(minimal_app, name='second')
    for m, u in (('get', '/api/settings/tv/search?q=x'), ('put', '/api/settings/tv/shows/1/match'),
                 ('post', '/api/settings/tv/unmatched/1/match')):
        assert getattr(other, m)(u, json={'tmdb_id': 1}, environ_base=REMOTE, headers=HDR).status_code == 403


def test_tmdb_busy_reads_plainly(owner, fake_tmdb):  # noqa: F811
    """Tester tv1-8646479 M5: a TMDB 429 (after the one retry) says so in the picker, the match and the scan."""
    fake_tmdb.fail = tv_tmdb.BUSY
    r = owner.get('/api/settings/tv/search?q=x', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 502 and r.get_json()['error'] == 'TMDB is busy. Try again later.'
    r = owner.post(f'/api/settings/tv/unmatched/{_uid("Mystery Show")}/match', json={'tmdb_id': 1001},
                   environ_base=REMOTE, headers=HDR)
    assert r.status_code == 502 and r.get_json()['error'] == 'TMDB is busy. Try again later.'
    tv_scan.run(now=5000.0)
    assert tv_scan.status()['error'] == 'TMDB is busy. Try again later.'
