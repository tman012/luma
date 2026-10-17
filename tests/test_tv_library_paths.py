"""Phase 1: TV library paths in tv_settings (own store) with Library paths'
checks, overlap across both stores and the downloads folder."""
import json
import os

import pytest

from app import file_management as fm, tv_settings, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client

URL = '/api/settings/tv/paths'
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


def _add(c, name, path, url=URL):
    return c.post(url, json={'name': name, 'path': str(path)}, environ_base=REMOTE, headers=HDR)


def test_add_edit_delete(owner, tv_env):
    r = _add(owner, 'TV', tv_env / 'TV')
    assert r.status_code == 201 and r.headers['Cache-Control'] == 'no-store'
    paths = r.get_json()['paths']
    assert [(p['name'], p['path'], p['status']) for p in paths] == [('TV', str(tv_env / 'TV'), 'ok')]
    pid = paths[0]['id']
    assert pid.startswith('t_') and len(pid) == 10
    assert json.loads(open(tv_settings.store_path()).read())['paths'][0]['id'] == pid
    r = owner.put(f'{URL}/{pid}', json={'name': 'Shows'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['paths'][0]['name'] == 'Shows'
    r = owner.delete(f'{URL}/{pid}', environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['paths'] == []
    assert owner.delete(f'{URL}/{pid}', environ_base=REMOTE, headers=HDR).status_code == 404


@pytest.mark.parametrize('raw,err', [
    ('', fm.REQUIRED), ('relative/tv', fm.INVALID_PATH), ('/x/../etc', fm.INVALID_PATH),
    ('/etc', fm.NOT_ALLOWED), ('/proc/1', fm.NOT_ALLOWED), ('NOPE', fm.NOT_FOUND),
])
def test_same_checks_as_library_paths(owner, tv_env, raw, err):
    if raw == 'NOPE':
        raw = str(tv_env / 'no-such')
    r = _add(owner, 'X', raw)
    assert r.status_code == 400 and r.get_json()['errors']['path'] == err


def test_name_checks(owner, tv_env):
    assert _add(owner, 'TV', tv_env / 'TV').status_code == 201
    r = _add(owner, 'tv', tv_env / 'Anime')
    assert r.status_code == 400 and r.get_json()['errors']['name'] == fm.NAME_TAKEN
    r = _add(owner, '  ', tv_env / 'Anime')
    assert r.get_json()['errors']['name'] == fm.REQUIRED


def test_overlap_within_tv(owner, tv_env):
    assert _add(owner, 'TV', tv_env / 'TV').status_code == 201
    (tv_env / 'TV' / 'Sub').mkdir()
    assert _add(owner, 'Dup', tv_env / 'TV').get_json()['errors']['path'] == fm.DUPLICATE
    assert _add(owner, 'In', tv_env / 'TV' / 'Sub').get_json()['errors']['path'] == fm.OVERLAPS
    assert _add(owner, 'Parent', tv_env).get_json()['errors']['path'] == fm.OVERLAPS


def test_overlap_with_movies_both_ways(owner, tv_env):
    assert _add(owner, 'Movies', tv_env / 'Movies', FM).status_code == 201
    assert _add(owner, 'TV', tv_env / 'Movies').get_json()['errors']['path'] == fm.DUPLICATE
    assert _add(owner, 'TV', tv_env).get_json()['errors']['path'] == fm.OVERLAPS
    assert _add(owner, 'TV', tv_env / 'TV').status_code == 201
    r = _add(owner, 'Same', tv_env / 'TV', FM)
    assert r.status_code == 400 and r.get_json()['errors']['path'] == fm.DUPLICATE
    (tv_env / 'TV' / 'Inner').mkdir()
    assert _add(owner, 'Inner', tv_env / 'TV' / 'Inner', FM).get_json()['errors']['path'] == fm.OVERLAPS


def test_symlink_into_movies_is_an_overlap(owner, tv_env):
    assert _add(owner, 'Movies', tv_env / 'Movies', FM).status_code == 201
    os.symlink(tv_env / 'Movies', tv_env / 'link')
    assert _add(owner, 'TV', tv_env / 'link').get_json()['errors']['path'] == fm.DUPLICATE


def test_downloads_folder_overlap(owner, tv_env, monkeypatch):
    (tv_env / 'TV' / 'dl').mkdir()
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(tv_env / 'TV' / 'dl'))
    assert _add(owner, 'TV', tv_env / 'TV').get_json()['errors']['path'] == fm.IN_DOWNLOADS
    assert _add(owner, 'Dl', tv_env / 'TV' / 'dl').get_json()['errors']['path'] == fm.IN_DOWNLOADS


def test_edit_checks_against_the_others_not_itself(owner, tv_env):
    pid = _add(owner, 'TV', tv_env / 'TV').get_json()['paths'][0]['id']
    _add(owner, 'Anime', tv_env / 'Anime')
    r = owner.put(f'{URL}/{pid}', json={'path': str(tv_env / 'TV')}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200
    r = owner.put(f'{URL}/{pid}', json={'path': str(tv_env / 'Anime')}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and r.get_json()['errors']['path'] == fm.DUPLICATE


def test_no_write_probe_and_read_only_is_accepted(owner, tv_env):
    """Phase 1 only reads TV folders: adding one creates nothing in it."""
    ro = tv_env / 'Kids TV'
    os.chmod(ro, 0o555)
    try:
        before = sorted(os.listdir(ro)), os.stat(ro).st_mtime_ns
        r = _add(owner, 'Kids', ro)
        assert r.status_code == 201
        assert (sorted(os.listdir(ro)), os.stat(ro).st_mtime_ns) == before
        if os.geteuid() != 0:
            assert r.get_json()['paths'][0]['status'] == 'read_only'
    finally:
        os.chmod(ro, 0o755)


def test_delete_in_use_by_fallback_is_409(owner, tv_env):
    pid = _add(owner, 'TV', tv_env / 'TV').get_json()['paths'][0]['id']
    d = tv_settings.load()
    d['fallback_path_id'] = pid
    tv_settings.save(d)
    assert owner.delete(f'{URL}/{pid}', environ_base=REMOTE, headers=HDR).status_code == 409


def test_delete_drops_its_rows_only(owner, tv_env):
    a = _add(owner, 'TV', tv_env / 'TV').get_json()['paths'][0]['id']
    b = [p for p in _add(owner, 'Anime', tv_env / 'Anime').get_json()['paths'] if p['name'] == 'Anime'][0]['id']
    with tv_store.write() as conn:
        for i, pid in ((1, a), (2, b)):
            conn.execute('INSERT INTO series (tmdb_id, title, path_id, folder, added_at) VALUES (?, ?, ?, ?, 0)',
                         (i, f's{i}', pid, f'f{i}'))
            conn.execute('INSERT INTO unmatched (path_id, folder, ts) VALUES (?, ?, 0)', (pid, f'u{i}'))
    assert owner.delete(f'{URL}/{a}', environ_base=REMOTE, headers=HDR).status_code == 200
    with tv_store.connect() as conn:
        assert [r[0] for r in conn.execute('SELECT path_id FROM series')] == [b]
        assert [r[0] for r in conn.execute('SELECT path_id FROM unmatched')] == [b]


def test_owner_only_minimal_only(minimal_app, tv_env, monkeypatch):
    _client(minimal_app)
    other = _client(minimal_app, name='second')
    assert _add(other, 'TV', tv_env / 'TV').status_code == 403
    assert _add(minimal_app.test_client(), 'TV', tv_env / 'TV').status_code == 401
    monkeypatch.delenv('LUMA_MINIMAL')
    assert _add(_client(minimal_app, name='third'), 'TV', tv_env / 'TV').status_code in (403, 404)


def test_bad_store_rows_are_dropped(tv_env):
    tv_settings.save({'paths': [{'id': 't_0000000a', 'name': 'A', 'path': '/media/a'},
                                {'id': 'bad', 'name': 'B', 'path': '/b'},
                                {'id': 't_0000000c', 'name': 'C', 'path': 'relative'}]})
    assert [p['id'] for p in tv_settings.load()['paths']] == ['t_0000000a']


# Tester tv1-8646479 M4: TV paths follow the folder picker's rule (inside the browse roots).

def test_path_outside_the_browse_roots_is_refused(owner, tv_env, tmp_path):
    outside = tmp_path / 'elsewhere' / 'TV'
    outside.mkdir(parents=True)
    r = _add(owner, 'TV', outside)
    assert r.status_code == 400 and r.get_json()['errors'] == {'path': fm.NOT_ALLOWED}
    link = tv_env / 'escape'
    link.symlink_to(outside)
    r = _add(owner, 'TV', link)
    assert r.status_code == 400 and r.get_json()['errors'] == {'path': fm.NOT_ALLOWED}
    assert _add(owner, 'TV', tv_env / 'TV').status_code == 201


def test_default_root_is_media(owner, tv_env, monkeypatch):
    monkeypatch.delenv('LUMA_BROWSE_ROOTS')
    r = _add(owner, 'TV', tv_env / 'TV')
    assert r.status_code == 400 and r.get_json()['errors'] == {'path': fm.NOT_ALLOWED}


def test_an_existing_path_outside_the_roots_can_still_be_renamed(owner, tv_env, tmp_path, monkeypatch):
    assert _add(owner, 'TV', tv_env / 'TV').status_code == 201
    pid = tv_settings.load()['paths'][0]['id']
    monkeypatch.setenv('LUMA_BROWSE_ROOTS', str(tmp_path / 'other'))
    (tmp_path / 'other').mkdir()
    r = owner.put(f'{URL}/{pid}', json={'name': 'Shows', 'path': str(tv_env / 'TV')}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['paths'][0]['name'] == 'Shows'
    r = owner.put(f'{URL}/{pid}', json={'path': str(tv_env / 'Anime')}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and r.get_json()['errors'] == {'path': fm.NOT_ALLOWED}
