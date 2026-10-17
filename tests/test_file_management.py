"""Settings > Library paths (minimal builds): its own empty store, path
checks with realpath, ordered rules with a fallback, the optional path map,
owner only, and never production's seeded media.paths."""

import json
import os
from datetime import date

import pytest

import app.main
from app import file_management as fm
from tests.test_home_rails import REMOTE, _client

HDR = {'Origin': 'http://localhost', 'Host': 'localhost'}
API = '/api/settings/library-paths'
RULES = '/api/settings/library-rules'
MAPPING = API + '/mapping'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setitem(fm._cfg, 'store_path', str(tmp_path / 'fm-store' / fm.STORE_NAME))
    return tmp_path / 'fm-store' / fm.STORE_NAME


@pytest.fixture
def media(tmp_path):
    """A mount parent like /media with library folders (spaces included)."""
    root = tmp_path / 'media'
    for name in ('Recently Released', 'Full Rips', '4K Movies', 'Movies'):
        (root / name).mkdir(parents=True)
    return root


@pytest.fixture
def minimal(test_app, monkeypatch, tmp_path, store):
    monkeypatch.setenv('LUMA_MINIMAL', '1')
    (tmp_path / 'index.html').write_text('<!doctype html><div id="root"></div>')
    monkeypatch.setitem(test_app.config, 'SPA_DIR', str(tmp_path))
    from app import start_flow
    monkeypatch.setattr(start_flow, '_load_state', lambda: {})
    return test_app


def _get(c):
    return c.get(API, environ_base=REMOTE, headers=HDR)


def _add(c, name, path):
    return c.post(API, json={'name': name, 'path': str(path)}, environ_base=REMOTE, headers=HDR)


def _rules(c, rules, fallback):
    return c.put(RULES, json={'rules': rules, 'fallback_path_id': fallback}, environ_base=REMOTE, headers=HDR)


# ------------------------------------------------------------------ access

def test_paths_allowlisted_in_minimal_and_not_signed_out():
    from app import minimal
    for p, m in ((API, 'GET'), (API, 'POST'), (API + '/p_0123abcd', 'PUT'), (API + '/p_0123abcd', 'DELETE'),
                 (RULES, 'PUT'), (MAPPING, 'PUT')):
        assert minimal.path_allowed(p, m), p
        assert p not in minimal.SIGNED_OUT_PATHS


def test_owner_only(minimal, media):
    _client(minimal)  # the owner
    for c in (_client(minimal, admin=True, name='second'), _client(minimal, admin=False, name='kid')):
        assert _get(c).status_code == 403
        assert _add(c, 'Movies', media / 'Movies').status_code == 403
        assert _rules(c, [], None).status_code == 403
        assert c.put(MAPPING, json={'transfer': 'hardlink'}, environ_base=REMOTE, headers=HDR).status_code == 403
        assert c.delete(API + '/p_0123abcd', environ_base=REMOTE, headers=HDR).status_code == 403
    anon = minimal.test_client()
    assert anon.get(API, environ_base=REMOTE, headers=HDR).status_code in (401, 302)
    assert fm.load()['paths'] == []


def test_service_key_is_not_the_owner(minimal, monkeypatch):
    _client(minimal)
    c = minimal.test_client()
    r = c.get(API, environ_base=REMOTE, headers={**HDR, 'X-API-Key': 'anything'})
    assert r.status_code in (401, 403)


def test_not_in_production_builds(test_app, monkeypatch, store):
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    c = _client(test_app)
    assert _get(c).status_code == 404
    assert _add(c, 'Movies', '/tmp').status_code == 404


def test_cross_site_write_refused(minimal, media):
    c = _client(minimal)
    r = c.post(API, json={'name': 'Movies', 'path': str(media / 'Movies')}, environ_base=REMOTE,
               headers={'Origin': 'https://evil.example', 'Host': 'localhost'})
    assert r.status_code == 403
    assert fm.load()['paths'] == []


# ------------------------------------------------------------------ fresh install

def test_fresh_install_starts_empty(minimal, store):
    c = _client(minimal)
    r = _get(c)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    j = r.get_json()
    assert j.pop('downloads')['status'] in ('ok', 'missing')
    assert j == {'paths': [], 'rules': [], 'fallback_path_id': None, 'path_map': [],
                 'transfer': 'copy', 'not_found': []}
    assert not store.exists()   # a read writes nothing


def test_ignores_seeded_media_paths_and_root_defaults(minimal, monkeypatch, store):
    """Prod's bootstrap seeds media.paths / media.types from the *_ROOT
    defaults; the new section never reads or shows them."""
    seeded = [{'name': n, 'path': p, 'type': t} for n, p, t in (
        ('Movies', '/media/movies', 'movies'), ('Recently Released', '/media/recent', 'recent'),
        ('TV Shows', '/media/tv', 'tv'), ('4K Movies', '/media/4k', '4k'),
        ('Full Rips', '/media/fullrips', 'fullrips'), ('4K TV', '/media/tv-4k', 'tv_4k'),
        ('Remux TV', '/media/tv-remux', 'tv_remux'))]
    real_get = app.main.settings.get
    reads = []

    def get(key, *a, **kw):
        if str(key).startswith('media.'):
            reads.append(key)
            return seeded if key == 'media.paths' else real_get(key, *a, **kw)
        return real_get(key, *a, **kw)
    monkeypatch.setattr(app.main.settings, 'get', get)
    for k in ('MOVIES_ROOT', 'RECENT_ROOT', 'FULLRIPS_ROOT', 'FOURKAY_ROOT'):
        monkeypatch.setenv(k, '/media/x')
    c = _client(minimal)
    body = _get(c).get_json()
    assert body['paths'] == [] and body['rules'] == [] and body['fallback_path_id'] is None
    assert '/media/' not in json.dumps(body)
    assert reads == []
    assert fm.effective_fallback(fm.load()) is None


# ------------------------------------------------------------------ add / edit / delete

def test_add_any_subfolder_of_a_mount_with_spaces(minimal, media, store):
    c = _client(minimal)
    r = _add(c, ' Recent ', media / 'Recently Released')
    assert r.status_code == 201, r.get_json()
    r = _add(c, 'Full rips', str(media / 'Full Rips') + '/')
    assert r.status_code == 201, r.get_json()
    paths = _get(c).get_json()['paths']
    assert [(p['name'], p['path'], p['status']) for p in paths] == [
        ('Recent', str(media / 'Recently Released'), 'ok'), ('Full rips', str(media / 'Full Rips'), 'ok')]
    assert all(p['id'].startswith('p_') for p in paths)
    assert oct(os.stat(store).st_mode & 0o777) == '0o600'
    assert not [f for f in os.listdir(media / 'Movies') if f.startswith('.luma-write-test')]


@pytest.mark.parametrize('raw,err', [
    ('', fm.REQUIRED), ('relative/path', fm.INVALID_PATH), ('/media/../etc', fm.INVALID_PATH),
    ('/bad\x00path', fm.INVALID_PATH), ('/bad\npath', fm.INVALID_PATH), (42, fm.INVALID_PATH),
    ('/' + 'a' * 2000, fm.INVALID_PATH), ('/', fm.NOT_ALLOWED), ('/etc', fm.NOT_ALLOWED),
    ('/proc/self', fm.NOT_ALLOWED), ('/app', fm.NOT_ALLOWED), ('/usr/local', fm.NOT_ALLOWED),
    ('/no/such/folder/here', fm.NOT_FOUND),
])
def test_path_checks(minimal, raw, err):
    c = _client(minimal)
    r = c.post(API, json={'name': 'X', 'path': raw}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and r.get_json()['errors']['path'] == err, r.get_json()
    assert fm.load()['paths'] == []


def test_data_and_cache_dirs_refused(minimal):
    from app import paths
    c = _client(minimal)
    for d in (paths.data_dir(), paths.cache_dir()):
        r = _add(c, 'X', d)
        assert r.status_code == 400 and r.get_json()['errors']['path'] == fm.NOT_ALLOWED


def test_symlink_escape_refused(minimal, media):
    (media / 'sneaky').symlink_to('/etc')
    c = _client(minimal)
    r = _add(c, 'Sneaky', media / 'sneaky')
    assert r.status_code == 400 and r.get_json()['errors']['path'] == fm.NOT_ALLOWED


def test_a_file_is_not_a_folder(minimal, media):
    (media / 'file.mkv').write_text('x')
    c = _client(minimal)
    assert _add(c, 'F', media / 'file.mkv').get_json()['errors']['path'] == fm.NOT_FOUND


@pytest.mark.skipif(hasattr(os, 'geteuid') and os.geteuid() == 0, reason='root can write anywhere')
def test_read_only_folder_refused(minimal, media):
    ro = media / 'Read only'
    ro.mkdir()
    ro.chmod(0o555)
    try:
        c = _client(minimal)
        assert _add(c, 'RO', ro).get_json()['errors']['path'] == fm.NOT_WRITABLE
    finally:
        ro.chmod(0o755)


def test_duplicates_and_nesting_between_configured_paths(minimal, media):
    (media / 'Movies' / 'Kids').mkdir()
    (media / 'alias').symlink_to(media / 'Movies')
    c = _client(minimal)
    assert _add(c, 'Movies', media / 'Movies').status_code == 201
    assert _add(c, 'Again', media / 'Movies').get_json()['errors']['path'] == fm.DUPLICATE
    assert _add(c, 'Alias', media / 'alias').get_json()['errors']['path'] == fm.DUPLICATE
    assert _add(c, 'Kids', media / 'Movies' / 'Kids').get_json()['errors']['path'] == fm.OVERLAPS
    assert _add(c, 'Everything', media).get_json()['errors']['path'] == fm.OVERLAPS
    assert _add(c, 'movies', media / 'Full Rips').get_json()['errors']['name'] == fm.NAME_TAKEN
    # Siblings under the same (unconfigured) mount parent are fine.
    assert _add(c, '4K', media / '4K Movies').status_code == 201


def test_name_checks(minimal, media):
    c = _client(minimal)
    assert _add(c, '', media / 'Movies').get_json()['errors']['name'] == fm.REQUIRED
    assert _add(c, 'x' * 41, media / 'Movies').get_json()['errors']['name'] == fm.INVALID
    assert _add(c, 'a\x07b', media / 'Movies').get_json()['errors']['name'] == fm.INVALID


def test_edit_and_delete(minimal, media):
    c = _client(minimal)
    pid = _add(c, 'Movies', media / 'Movies').get_json()['paths'][0]['id']
    r = c.put(f'{API}/{pid}', json={'name': 'Older', 'path': str(media / '4K Movies')}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    assert [(p['name'], p['path']) for p in r.get_json()['paths']] == [('Older', str(media / '4K Movies'))]
    r = c.put(f'{API}/{pid}', json={'path': '/etc'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and fm.load()['paths'][0]['path'] == str(media / '4K Movies')
    assert c.put(f'{API}/p_00000000', json={'name': 'x'}, environ_base=REMOTE, headers=HDR).status_code == 404
    assert c.delete(f'{API}/{pid}', environ_base=REMOTE, headers=HDR).status_code == 200
    assert fm.load()['paths'] == []


def test_delete_in_use_is_409(minimal, media):
    c = _client(minimal)
    a = _add(c, 'Recent', media / 'Recently Released').get_json()['paths'][0]['id']
    b = _add(c, 'Movies', media / 'Movies').get_json()['paths'][1]['id']
    assert _rules(c, [{'path_id': a, 'released_within': {'n': 1, 'unit': 'years'}}], b).status_code == 200
    for pid in (a, b):
        r = c.delete(f'{API}/{pid}', environ_base=REMOTE, headers=HDR)
        assert r.status_code == 409 and r.get_json()['error'] == fm.IN_USE
    assert len(fm.load()['paths']) == 2


# ------------------------------------------------------------------ rules

def _two(c, media):
    a = _add(c, 'Recent', media / 'Recently Released').get_json()['paths'][0]['id']
    b = _add(c, 'Movies', media / 'Movies').get_json()['paths'][1]['id']
    return a, b


def test_rules_save_in_order_with_fallback(minimal, media):
    c = _client(minimal)
    a, b = _two(c, media)
    rules = [{'path_id': a, 'released_within': {'n': 12, 'unit': 'months'}},
             {'path_id': b, 'enabled': False, 'resolution': ['1080p', '2160p', '1080p'], 'source': ['remux']}]
    r = _rules(c, rules, b)
    assert r.status_code == 200, r.get_json()
    saved = r.get_json()['rules']
    assert [x['path_id'] for x in saved] == [a, b]
    assert saved[0]['enabled'] is True and saved[1]['enabled'] is False
    assert saved[1]['resolution'] == ['2160p', '1080p']     # canonical, no duplicates
    assert r.get_json()['fallback_path_id'] == b
    # ids are kept on a reorder
    ids = [x['id'] for x in saved]
    r = _rules(c, list(reversed(saved)), b)
    assert [x['id'] for x in r.get_json()['rules']] == list(reversed(ids))


def test_rules_need_a_fallback(minimal, media):
    c = _client(minimal)
    a, _b = _two(c, media)
    r = _rules(c, [{'path_id': a}], None)
    assert r.status_code == 400 and r.get_json()['errors'] == {'fallback_path_id': fm.REQUIRED}
    assert _rules(c, [], None).status_code == 200   # no rules: optional


@pytest.mark.parametrize('rule,key', [
    ({'path_id': 'p_ffffffff'}, 'rules.0.path_id'),
    ({}, 'rules.0.path_id'),
    ({'PATH': 1, 'path_id': None}, 'rules.0.path_id'),
    ({'path_id': '$A', 'resolution': ['8k']}, 'rules.0.resolution'),
    ({'path_id': '$A', 'source': ['cam']}, 'rules.0.source'),
    ({'path_id': '$A', 'source': 'remux'}, 'rules.0.source'),
    ({'path_id': '$A', 'released_within': {'n': 0, 'unit': 'months'}}, 'rules.0.released_within'),
    ({'path_id': '$A', 'released_within': {'n': 1, 'unit': 'days'}}, 'rules.0.released_within'),
    ({'path_id': '$A', 'released_within': {'n': True, 'unit': 'years'}}, 'rules.0.released_within'),
    ({'path_id': '$A', 'released_within': {'n': 51, 'unit': 'years'}}, 'rules.0.released_within'),
    ({'path_id': '$A', 'enabled': 'yes'}, 'rules.0.enabled'),
])
def test_rule_checks(minimal, media, rule, key):
    c = _client(minimal)
    a, b = _two(c, media)
    rule = {k: (a if v == '$A' else v) for k, v in rule.items()}
    r = _rules(c, [rule], b)
    assert r.status_code == 400 and key in r.get_json()['errors'], r.get_json()
    assert fm.load()['rules'] == []


def test_too_many_rules(minimal, media):
    c = _client(minimal)
    a, b = _two(c, media)
    r = _rules(c, [{'path_id': a}] * (fm.MAX_RULES + 1), b)
    assert r.status_code == 400 and r.get_json()['errors'] == {'rules': fm.TOO_MANY}


def _data(paths, rules=(), fallback=None):
    return {'paths': [{'id': i, 'name': i, 'path': '/x/' + i} for i in paths], 'rules': list(rules),
            'fallback_path_id': fallback, 'path_map': [], 'transfer': 'copy', 'jobs': {}}


def _rule(pid, enabled=True, **kw):
    return {'id': 'r_' + pid[-8:], 'enabled': enabled, 'path_id': pid,
            'released_within': kw.get('within'), 'resolution': kw.get('res', []), 'source': kw.get('src', [])}


TODAY = date(2026, 10, 17)
P = ('p_00000001', 'p_00000002', 'p_00000003', 'p_00000004')


def test_taylors_example_first_match_wins():
    recent, r1080, r4k, older = P
    data = _data(P, [
        _rule(recent, within={'n': 1, 'unit': 'years'}),
        _rule(r1080, res=['1080p'], src=['remux']),
        _rule(r4k, res=['2160p'], src=['remux']),
    ], fallback=older)
    pick = lambda **info: fm.pick_destination(data, info, TODAY)['id']
    assert pick(release_date=date(2026, 3, 1), resolution='2160p', source='remux') == recent
    assert pick(release_date=date(2025, 10, 17), resolution='720p', source='web') == recent   # boundary
    assert pick(release_date=date(2025, 10, 16), resolution='1080p', source='remux') == r1080
    assert pick(release_date=date(1999, 1, 1), resolution='2160p', source='remux') == r4k
    assert pick(release_date=date(1999, 1, 1), resolution='1080p', source='bluray') == older
    assert pick(release_date=None, resolution='1080p', source='web') == older     # no TMDB date
    assert pick(resolution=None, source=None) == older


def test_disabled_rules_are_skipped_and_months_work():
    a, b, c, _ = P
    data = _data(P[:3], [_rule(a, enabled=False), _rule(b, within={'n': 6, 'unit': 'months'})], fallback=c)
    assert fm.pick_destination(data, {'release_date': date(2026, 4, 17)}, TODAY)['id'] == b
    assert fm.pick_destination(data, {'release_date': date(2026, 4, 16)}, TODAY)['id'] == c


def test_month_arithmetic_clamps_days():
    assert fm.months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert fm.months_before(date(2026, 1, 15), 13) == date(2024, 12, 15)


def test_one_path_works_without_rules_or_fallback():
    data = _data(P[:1])
    assert fm.pick_destination(data, {}, TODAY)['id'] == P[0]
    assert fm.pick_destination(_data(P[:2]), {}, TODAY) is None   # two paths, no fallback
    assert fm.pick_destination(_data([]), {}, TODAY) is None


@pytest.mark.parametrize('name,res,src', [
    ('Dune.2021.2160p.UHD.BluRay.REMUX.HDR.HEVC.Atmos-GROUP', '2160p', 'remux'),
    ('Heat.1995.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1', '1080p', 'remux'),
    ('Heat.1995.1080p.BluRay.x264-GROUP', '1080p', 'bluray'),
    ('The.Manchurian.Candidate.1962.1080p.WEB-DL.DDP2.0.H.264', '1080p', 'web'),
    ('The Manchurian Candidate 2004 720p WEBRip x264', '720p', 'web'),
    ('Movie.2020.WEB.H264-GROUP', None, 'web'),
    ('Movie.2020.720p.HDTV.x264', '720p', 'hdtv'),
    ('Old.Movie.1950.DVDRip.XviD', 'sd', None),
    ('Old.Movie.1950.576p.BDRip', 'sd', 'bluray'),
    ("Charlotte's.Web.2006.1080p.BluRay.x264", '1080p', 'bluray'),
    ('Pal.Joey.1957.1080p.BluRay', '1080p', 'bluray'),
    ('4K.Wars.2019.1080p.WEBRip', '1080p', 'web'),
    ('Some_Movie_2019_2160p_UHD_WEB-DL', '2160p', 'web'),
])
def test_release_name_classifier(name, res, src):
    assert fm.classify(name) == {'resolution': res, 'source': src}


def test_classifier_falls_back_to_the_file_name():
    assert fm.classify('Movie (2020)', 'Movie.2020.2160p.REMUX.mkv') == {'resolution': '2160p', 'source': 'remux'}
    assert fm.classify('Movie.2020.1080p.WEB-DL', 'movie.2160p.remux.mkv') == {'resolution': '1080p', 'source': 'web'}


# ------------------------------------------------------------------ naming

@pytest.mark.parametrize('title,expect', [
    ('Mission: Impossible', 'Mission- Impossible'),
    ('AC/DC: Let There Be Rock', 'ACDC- Let There Be Rock'),
    ('..', 'Movie'), ('.', 'Movie'), ('', 'Movie'), (None, 'Movie'), ('../../etc', 'etc'),
    ('.hidden', 'hidden'), ('a\x00b\nc', 'abc'), ('What?*<>|"', 'What'), ('   spaced   out  ', 'spaced out'),
])
def test_safe_component(title, expect):
    assert fm.safe_component(title) == expect


def test_safe_component_caps_length():
    assert len(fm.safe_component('é' * 500).encode()) <= 180


def test_movie_names():
    assert fm.movie_names('Heat', '1995', '.MKV') == ('Heat (1995)', 'Heat (1995).mkv')
    assert fm.movie_names('Heat', '', '.mkv', part=2) == ('Heat', 'Heat - pt2.mkv')
    assert fm.movie_names('Heat', '19xx', '.mkv/../x') == ('Heat', 'Heat')


# ------------------------------------------------------------------ path map / transfer

def test_mapping_and_transfer(minimal, media, tmp_path):
    dl = tmp_path / 'downloads'
    dl.mkdir()
    c = _client(minimal)
    r = c.put(MAPPING, json={'path_map': [{'remote': '/data/torrents/', 'local': str(dl)}], 'transfer': 'hardlink'},
              environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['path_map'] == [{'remote': '/data/torrents', 'local': str(dl)}]
    assert r.get_json()['transfer'] == 'hardlink'
    for body, key in (({'transfer': 'move'}, 'transfer'),
                      ({'path_map': [{'remote': 'rel', 'local': str(dl)}]}, 'path_map.0.remote'),
                      ({'path_map': [{'remote': '/a', 'local': '/etc'}]}, 'path_map.0.local'),
                      ({'path_map': [{'remote': '/a', 'local': '/nope/x'}]}, 'path_map.0.local'),
                      ({'path_map': [{'remote': '/a/../b', 'local': str(dl)}]}, 'path_map.0.remote'),
                      ({'path_map': 'x'}, 'path_map')):
        r = c.put(MAPPING, json=body, environ_base=REMOTE, headers=HDR)
        assert r.status_code == 400 and key in r.get_json()['errors'], (body, r.get_json())
    assert fm.load()['transfer'] == 'hardlink'


def test_map_path_longest_prefix_whole_segments():
    m = [{'remote': '/data', 'local': '/downloads'}, {'remote': '/data/movies', 'local': '/mnt/m'}]
    assert fm.map_path('/data/movies/X/x.mkv', m) == '/mnt/m/X/x.mkv'
    assert fm.map_path('/data/tv/Y', m) == '/downloads/tv/Y'
    assert fm.map_path('/data', m) == '/downloads'
    assert fm.map_path('/database/x', m) == '/database/x'
    assert fm.map_path('/other/x', m) == '/other/x'
    assert fm.map_path('/downloads/x', []) == '/downloads/x'


def test_unreadable_store_reads_empty(minimal, store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text('{not json')
    assert fm.load()['paths'] == []
    assert not store.exists()   # quarantined, not trusted
