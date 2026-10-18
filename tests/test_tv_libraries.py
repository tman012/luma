"""TV libraries are Library paths selected in Settings > TV (references to
file_management paths, never folders of TV's own), the reusable selection
API (app/library_selection), the one-time move of earlier builds' TV paths,
and what happens when a selected Library path is removed."""
import json
import os

import pytest

from app import file_management as fm, library_selection, tv_scan, tv_settings, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.tv_fake import fake_tmdb  # noqa: F401

URL = '/api/settings/tv/libraries'
FM = '/api/settings/library-paths'


@pytest.fixture
def tv_env(tmp_path, monkeypatch, store):  # noqa: F811
    monkeypatch.setitem(tv_settings._cfg, 'store_path', str(tmp_path / 'tvs' / tv_settings.STORE_NAME))
    monkeypatch.setitem(tv_store._cfg, 'path', str(tmp_path / 'tvdb' / 'tv.db'))
    tv_store._ready.clear()
    dl = tmp_path / 'dl'
    dl.mkdir()
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(dl))
    media = tmp_path / 'media'
    monkeypatch.setenv('LUMA_BROWSE_ROOTS', str(media))
    for n in ('TV', 'Anime', 'Movies', 'Kids TV'):
        (media / n).mkdir(parents=True)
    yield media
    tv_store._ready.clear()


@pytest.fixture
def owner(minimal_app, tv_env):
    return _client(minimal_app)


FILMS = 'p_f11f11f1'


@pytest.fixture
def films(tv_env):
    """Movies already file into a 'Films' path they chose (explicit fallback),
    so a TV path is never Movies' implicit single fallback (Reviewer F1/F2)."""
    (tv_env / 'Films').mkdir()
    fm.save({**fm.load(), 'paths': [{'id': FILMS, 'name': 'Films', 'path': str(tv_env / 'Films')}],
             'fallback_path_id': FILMS})
    return FILMS


def _add(c, name, path):
    r = c.post(FM, json={'name': name, 'path': str(path)}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 201, r.get_json()
    return next(p['id'] for p in r.get_json()['paths'] if p['name'] == name)


def _put(c, selected):
    return c.put(URL, json={'selected': selected}, environ_base=REMOTE, headers=HDR)


def _get(c, url=URL):
    return c.get(url, environ_base=REMOTE, headers=HDR)


def _raw_store():
    return json.loads(open(tv_settings.store_path()).read())


# ---------------------------------------------------------------- selector

def test_lists_library_paths_and_selects_by_reference(owner, tv_env, films):
    tv = _add(owner, 'TV', tv_env / 'TV')
    anime = _add(owner, 'Anime', tv_env / 'Anime')
    movies = _add(owner, 'Movies', tv_env / 'Movies')
    r = _get(owner)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    assert r.get_json() == {'selected': [], 'libraries': [
        {'id': i, 'name': n, 'path': str(tv_env / n), 'status': 'ok', 'selected': False}
        for i, n in ((films, 'Films'), (tv, 'TV'), (anime, 'Anime'), (movies, 'Movies'))]}
    r = _put(owner, [anime, tv])
    assert r.status_code == 200 and r.get_json()['selected'] == [tv, anime]      # Library paths order
    assert [x['selected'] for x in r.get_json()['libraries']] == [False, True, True, False]
    assert _raw_store()['libraries'] == [tv, anime]                             # ids only, no folders
    assert 'path' not in json.dumps(_raw_store()['libraries'])
    paths = _get(owner, '/api/settings/tv').get_json()['paths']
    assert [(p['id'], p['name'], p['path'], p['status']) for p in paths] == [
        (tv, 'TV', str(tv_env / 'TV'), 'ok'), (anime, 'Anime', str(tv_env / 'Anime'), 'ok')]
    assert [p['id'] for p in tv_settings.libraries()] == [tv, anime]
    assert _put(owner, []).get_json()['selected'] == [] and tv_settings.libraries() == []


def test_a_rename_in_library_paths_shows_in_tv(owner, tv_env, films):
    tv = _add(owner, 'TV', tv_env / 'TV')
    _put(owner, [tv])
    owner.put(f'{FM}/{tv}', json={'name': 'Shows'}, environ_base=REMOTE, headers=HDR)
    assert [p['name'] for p in _get(owner, '/api/settings/tv').get_json()['paths']] == ['Shows']


@pytest.mark.parametrize('body,err', [
    ({'selected': ['p_deadbeef']}, library_selection.NOT_A_LIBRARY),
    ({'selected': 'p_deadbeef'}, library_selection.INVALID),
    ({'selected': [1]}, library_selection.INVALID),
    ({'selected': None}, library_selection.INVALID),
    ({'selected': [], 'paths': []}, library_selection.INVALID),
    ({}, library_selection.INVALID),
])
def test_only_current_library_paths(owner, tv_env, films, body, err):
    tv = _add(owner, 'TV', tv_env / 'TV')
    _put(owner, [tv])
    r = owner.put(URL, json=body, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and r.get_json() == {'errors': {'selected': err}}
    assert tv_settings.load()['libraries'] == [tv]                       # nothing saved


def test_a_repeated_id_counts_once(owner, tv_env, films):
    tv = _add(owner, 'TV', tv_env / 'TV')
    assert _put(owner, [tv, tv, tv]).get_json()['selected'] == [tv]
    assert tv_settings.load()['libraries'] == [tv]


def test_check_selection_helper():
    paths = [{'id': f'p_0000000{i}', 'name': str(i), 'path': f'/m/{i}'} for i in range(3)]
    assert library_selection.check_selection(['p_00000002', 'p_00000000'], paths) == (['p_00000000', 'p_00000002'], None)
    assert library_selection.check_selection(['p_00000009'], paths) == (None, library_selection.NOT_A_LIBRARY)
    many = [{'id': f'p_{i:08x}', 'name': str(i), 'path': f'/m/{i}'} for i in range(library_selection.MAX_SELECTED + 1)]
    assert library_selection.check_selection([p['id'] for p in many], many) == (None, library_selection.TOO_MANY)
    assert library_selection.resolve(['p_00000001', 'p_00000009'], paths) == [paths[1]]


def test_store_keeps_only_library_ids(tv_env):
    tv_settings.save({'libraries': ['p_0000000a', 'bad', 7, 'p_0000000a', 't_0000000b', 'p_0000000c']})
    assert tv_settings.load()['libraries'] == ['p_0000000a', 'p_0000000c']


def test_signed_in_owner_only(minimal_app, tv_env, monkeypatch):
    c = _client(minimal_app)
    tv = _add(c, 'TV', tv_env / 'TV')
    other = _client(minimal_app, name='second')
    assert _get(other).status_code == 403
    r = _put(other, [tv])
    assert r.status_code == 403 and 'sign_in' not in r.get_json()
    r = _put(minimal_app.test_client(), [tv])
    assert r.status_code == 401 and r.get_json()['sign_in'] is True
    assert _get(minimal_app.test_client()).status_code == 401
    assert tv_settings.load()['libraries'] == []
    monkeypatch.delenv('LUMA_MINIMAL')
    assert _put(_client(minimal_app, name='third'), [tv]).status_code in (403, 404)
    assert _get(_client(minimal_app, name='fourth')).status_code in (403, 404)


def test_tv_has_no_add_path_routes_any_more(owner, tv_env):
    for method, url in (('post', '/api/settings/tv/paths'), ('put', '/api/settings/tv/paths/t_00000001'),
                        ('delete', '/api/settings/tv/paths/t_00000001')):
        r = getattr(owner, method)(url, json={'name': 'TV', 'path': str(tv_env / 'TV')}, environ_base=REMOTE, headers=HDR)
        assert r.status_code in (404, 405)
    assert fm.load()['paths'] == [] and tv_settings.load()['libraries'] == []


def test_selecting_writes_nothing_in_the_folder(owner, tv_env):
    tv = _add(owner, 'TV', tv_env / 'TV')
    before = sorted(os.listdir(tv_env / 'TV')), os.stat(tv_env / 'TV').st_mtime_ns
    _put(owner, [tv])
    _get(owner)
    assert (sorted(os.listdir(tv_env / 'TV')), os.stat(tv_env / 'TV').st_mtime_ns) == before


def test_movies_and_tv_never_share_a_library_path(owner, tv_env, films):
    """PM (M22): a path is Movies or TV. Choosing a TV path for Movies is refused."""
    tv = _add(owner, 'TV', tv_env / 'TV')
    _put(owner, [tv])
    _add(owner, 'Anime', tv_env / 'Anime')
    r = owner.put('/api/settings/library-rules', json={'rules': [], 'fallback_path_id': tv}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and r.get_json()['errors'] == {'fallback_path_id': 'This path is used by TV'}
    assert tv_settings.load()['libraries'] == [tv]


# ---------------------------------------------------------------- removed later

def _seed_rows(pid, n):
    with tv_store.write() as conn:
        conn.execute('INSERT INTO series (tmdb_id, title, path_id, folder, added_at) VALUES (?, ?, ?, ?, 0)',
                     (n, f's{n}', pid, f'f{n}'))
        conn.execute('INSERT INTO unmatched (path_id, folder, ts) VALUES (?, ?, 0)', (pid, f'u{n}'))


def _tv_rows():
    with tv_store.connect() as conn:
        return (sorted(r[0] for r in conn.execute('SELECT path_id FROM series')),
                sorted(r[0] for r in conn.execute('SELECT path_id FROM unmatched')))


def test_removing_a_selected_library_path_drops_the_selection_keeps_tv_db(owner, tv_env, films):
    tv = _add(owner, 'TV', tv_env / 'TV')
    anime = _add(owner, 'Anime', tv_env / 'Anime')
    _put(owner, [tv, anime])
    _seed_rows(tv, 1)
    _seed_rows(anime, 2)
    r = owner.delete(f'{FM}/{tv}', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    assert tv_settings.load()['libraries'] == [anime]
    assert _get(owner).get_json()['selected'] == [anime]
    assert _tv_rows() == (sorted([tv, anime]), sorted([tv, anime]))      # tv.db keeps everything
    view = tv_scan.library_view()
    assert [s['path_id'] for s in view['shows']] == [anime]               # only selected libraries show
    assert [u['path_id'] for u in view['unmatched']] == [anime]
    assert sorted(os.listdir(tv_env / 'TV')) == []                       # folder untouched


def test_deselecting_hides_and_reselecting_restores(owner, tv_env, films):
    tv = _add(owner, 'TV', tv_env / 'TV')
    _put(owner, [tv])
    _seed_rows(tv, 1)
    _put(owner, [])
    assert tv_scan.library_view()['shows'] == [] and _tv_rows() == ([tv], [tv])
    _put(owner, [tv])
    assert [s['title'] for s in tv_scan.library_view()['shows']] == ['s1']


def test_a_selection_pointing_at_a_vanished_path_is_ignored(tv_env):
    """Even if the hook never ran (an edit by hand), readers only see current Library paths."""
    tv_settings.save({'libraries': ['p_0000dead']})
    assert tv_settings.libraries() == [] and not tv_scan.due(1e9)


# ---------------------------------------------------------------- migration

def _legacy(*paths, **extra):
    tv_settings.save({'version': 1, **extra})
    raw = _raw_store()
    raw['paths'] = [{'id': i, 'name': n, 'path': str(p)} for i, n, p in paths]
    tv_settings.store_path().write_text(json.dumps(raw))


def test_migration_matching_path_becomes_a_selection(tv_env, films):
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000001', 'name': 'Shows', 'path': str(tv_env / 'TV')}] + fm.load()['paths']})
    _legacy(('t_0000000a', 'TV', str(tv_env / 'TV') + '/'))                  # same folder after normalizing
    _seed_rows('t_0000000a', 1)
    assert tv_settings.migrate() == {'t_0000000a': 'p_00000001'}
    assert fm.load()['paths'][0] == {'id': 'p_00000001', 'name': 'Shows', 'path': str(tv_env / 'TV')}
    assert len(fm.load()['paths']) == 2
    assert tv_settings.load()['libraries'] == ['p_00000001'] and tv_settings.load()['paths'] == []
    assert _tv_rows() == (['p_00000001'], ['p_00000001'])


def test_migration_symlinked_and_dotted_paths_match(tv_env):
    os.symlink(tv_env / 'TV', tv_env / 'tv-link')
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000001', 'name': 'TV', 'path': str(tv_env / 'TV')},
                                    {'id': 'p_00000002', 'name': 'Anime', 'path': str(tv_env / 'Anime')}]})
    _legacy(('t_0000000a', 'Link', tv_env / 'tv-link'), ('t_0000000b', 'A', f'{tv_env}/Movies/../Anime'))
    assert tv_settings.migrate() == {'t_0000000a': 'p_00000001', 't_0000000b': 'p_00000002'}
    assert len(fm.load()['paths']) == 2


def test_migration_no_match_is_added_to_library_paths(tv_env):
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000001', 'name': 'TV', 'path': str(tv_env / 'Movies')}]})
    os.chmod(tv_env / 'Kids TV', 0o555)
    try:
        before = sorted(os.listdir(tv_env / 'Kids TV')), os.stat(tv_env / 'Kids TV').st_mtime_ns
        _legacy(('t_0000000a', 'TV', tv_env / 'TV'), ('t_0000000b', 'Kids', tv_env / 'Kids TV'),
                ('t_0000000c', 'Gone', tv_env / 'unplugged'))
        _seed_rows('t_0000000b', 2)
        mapping = tv_settings.migrate()
        paths = fm.load()['paths']
        assert [(p['name'], p['path']) for p in paths] == [
            ('TV', str(tv_env / 'Movies')), ('TV (TV)', str(tv_env / 'TV')),       # the name was taken
            ('Kids', str(tv_env / 'Kids TV')), ('Gone', str(tv_env / 'unplugged'))]  # kept even if missing
        assert all(fm._ID_RE.match(p['id']) for p in paths)
        assert mapping == {'t_0000000a': paths[1]['id'], 't_0000000b': paths[2]['id'], 't_0000000c': paths[3]['id']}
        assert tv_settings.load()['libraries'] == [p['id'] for p in paths[1:]]
        assert _tv_rows() == ([paths[2]['id']], [paths[2]['id']])
        # no write probe or anything else in the folders themselves
        assert (sorted(os.listdir(tv_env / 'Kids TV')), os.stat(tv_env / 'Kids TV').st_mtime_ns) == before
        assert not (tv_env / 'unplugged').exists()
    finally:
        os.chmod(tv_env / 'Kids TV', 0o755)


def test_migration_duplicates_select_once(tv_env, films):
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000001', 'name': 'TV', 'path': str(tv_env / 'TV')}] + fm.load()['paths']})
    _legacy(('t_0000000a', 'TV', tv_env / 'TV'), ('t_0000000b', 'TV again', f'{tv_env}/TV/'),
            ('t_0000000c', 'Anime', tv_env / 'Anime'), ('t_0000000d', 'Anime 2', f'{tv_env}//Anime'))
    _seed_rows('t_0000000a', 1)
    with tv_store.write() as conn:                    # the second copy saw the same folder
        conn.execute("INSERT INTO unmatched (path_id, folder, ts) VALUES ('t_0000000b', 'u1', 0)")
        conn.execute("INSERT INTO unmatched (path_id, folder, ts) VALUES ('t_0000000b', 'other', 0)")
    mapping = tv_settings.migrate()
    paths = fm.load()['paths']
    assert len(paths) == 3 and paths[2]['name'] == 'Anime'
    assert mapping == {'t_0000000a': 'p_00000001', 't_0000000b': 'p_00000001',
                       't_0000000c': paths[2]['id'], 't_0000000d': paths[2]['id']}
    assert tv_settings.load()['libraries'] == ['p_00000001', paths[2]['id']]
    with tv_store.connect() as conn:
        assert sorted(tuple(r) for r in conn.execute('SELECT path_id, folder FROM unmatched')) == [
            ('p_00000001', 'other'), ('p_00000001', 'u1')]


def test_migration_twice_changes_nothing(tv_env):
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000001', 'name': 'TV', 'path': str(tv_env / 'TV')}]})
    _legacy(('t_0000000a', 'TV', tv_env / 'TV'), ('t_0000000b', 'Anime', tv_env / 'Anime'),
            fallback_path_id='t_0000000b', rules=[{'id': 'r_1', 'path_id': 't_0000000a'}])
    _seed_rows('t_0000000a', 1)
    first = tv_settings.migrate()
    snap = (fm.store_path().read_bytes(), tv_settings.store_path().read_bytes(), _tv_rows())
    assert tv_settings.migrate() == {} and tv_settings.migrate() == {}
    assert (fm.store_path().read_bytes(), tv_settings.store_path().read_bytes(), _tv_rows()) == snap
    st = tv_settings.load()
    assert st['fallback_path_id'] == first['t_0000000b'] and st['rules'][0]['path_id'] == 'p_00000001'


def test_migration_rerun_after_a_crash_matches_what_it_added(tv_env, monkeypatch):
    _legacy(('t_0000000a', 'TV', tv_env / 'TV'))
    real = tv_settings._move_rows
    monkeypatch.setattr(tv_settings, '_move_rows', lambda old, new: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError):
        tv_settings.migrate()
    added = fm.load()['paths']
    assert len(added) == 1 and tv_settings.load()['paths']                 # TV paths still there
    monkeypatch.setattr(tv_settings, '_move_rows', real)
    assert tv_settings.migrate() == {'t_0000000a': added[0]['id']}
    assert fm.load()['paths'] == added and tv_settings.load()['libraries'] == [added[0]['id']]


def test_migration_runs_on_the_first_settings_read_not_the_scan(owner, tv_env):
    _legacy(('t_0000000a', 'TV', tv_env / 'TV'))
    assert tv_scan.scan_all(now=1.0)['paths'] == 0                         # the scan never writes stores
    assert tv_settings.load()['paths'] and fm.load()['paths'] == []
    r = _get(owner)
    assert r.status_code == 200 and [x['name'] for x in r.get_json()['libraries']] == ['TV']
    assert r.get_json()['selected'] == [fm.load()['paths'][0]['id']]


def test_no_tv_paths_no_migration(tv_env):
    assert tv_settings.migrate() == {}
    assert not tv_settings.store_path().exists() and fm.load()['paths'] == []


def test_main_migrates_on_start():
    src = open(os.path.join(os.path.dirname(__file__), '..', 'app', 'main.py')).read()
    assert '_tv_settings.migrate_on_start()' in src


# ---------------------------------------------------------------- added back later

def test_a_removed_library_added_back_keeps_its_shows(owner, fake_tmdb, tv_env, films, tmp_path):  # noqa: F811
    from tests.tv_tree import build
    root = build(tv_env / 'TV', outside=tmp_path / 'outside')
    first = _add(owner, 'TV', root)
    _put(owner, [first])
    tv_scan.scan_all(now=1000.0)
    with tv_store.connect() as conn:
        before = {r['tmdb_id']: r['id'] for r in conn.execute('SELECT id, tmdb_id FROM series')}
        files = conn.execute('SELECT COUNT(*) FROM episode_files').fetchone()[0]
    assert before and owner.delete(f'{FM}/{first}', environ_base=REMOTE, headers=HDR).status_code == 200
    assert tv_settings.load()['libraries'] == [] and tv_scan.library_view()['shows'] == []
    again = _add(owner, 'TV', root)                       # a new Library path id for the same folder
    _put(owner, [again])
    tv_scan.scan_all(now=2000.0)
    with tv_store.connect() as conn:
        after = {r['tmdb_id']: r['id'] for r in conn.execute('SELECT id, tmdb_id FROM series')}
        assert {r[0] for r in conn.execute('SELECT path_id FROM series')} == {again}
        assert {r[0] for r in conn.execute('SELECT path_id FROM episode_files')} == {again}
        assert conn.execute('SELECT COUNT(*) FROM episode_files').fetchone()[0] == files
    assert after == before                                # same rows: history and settings kept
    view = tv_scan.library_view()
    assert len(view['shows']) == len(before)
    assert not [u for u in view['unmatched'] if u['reason'] == 'duplicate']
