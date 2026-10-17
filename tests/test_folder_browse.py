"""Folder picker: browses /media (Ops mounts host /mnt/media at /media), the
downloads folder and the configured library roots, never outside them, no
symlink escapes. LUMA_BROWSE_ROOTS overrides /media; no /mnt hard-coded."""
import os
from pathlib import Path

import pytest

from app import file_management as fm, tv_settings
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_home_rails import REMOTE, _client
from tests.test_tv_library_paths import tv_env  # noqa: F401

URL = '/api/settings/library-paths/browse'
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tree(tv_env, tmp_path, monkeypatch):  # noqa: F811
    media = tv_env                                      # stands in for /media
    (media / 'Video' / 'Movies').mkdir(parents=True)
    (media / '.hidden').mkdir()
    (media / 'file.txt').write_text('x')
    outside = tmp_path / 'outside'
    (outside / 'secret').mkdir(parents=True)
    os.symlink(outside, media / 'escape')               # points out: never shown
    os.symlink(media / 'Video', media / 'VideoLink')    # stays in: fine
    monkeypatch.setenv('LUMA_BROWSE_ROOTS', str(media))
    return media


@pytest.fixture
def owner(minimal_app, tree):
    return _client(minimal_app)


def _get(c, path=None):
    return c.get(URL, query_string={'path': path} if path else {}, environ_base=REMOTE, headers=HDR)


def test_roots_are_env_plus_downloads(owner, tree):
    r = _get(owner)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    body = r.get_json()
    assert body['path'] is None
    assert [d['path'] for d in body['dirs']] == sorted([str(tree), os.path.realpath(fm.downloads_root())])


def test_lists_folders_only_no_hidden_no_escape(owner, tree):
    body = _get(owner, str(tree)).get_json()
    assert body['parent'] is None                        # at a root
    assert [d['name'] for d in body['dirs']] == ['Anime', 'Kids TV', 'Movies', 'TV', 'Video', 'VideoLink']
    inner = _get(owner, str(tree / 'Video')).get_json()
    assert inner['parent'] == str(tree) and inner['dirs'] == [{'name': 'Movies', 'path': str(tree / 'Video' / 'Movies')}]


@pytest.mark.parametrize('rel', ['escape', 'escape/secret'])
def test_symlink_escape_refused(owner, tree, rel):
    r = _get(owner, str(tree / rel))
    assert r.status_code == 400 and r.get_json() == {'error': fm.NOT_ALLOWED}


@pytest.mark.parametrize('p,err', [('/etc', fm.NOT_ALLOWED), ('/', fm.NOT_ALLOWED), ('/tmp', fm.NOT_ALLOWED),
                                   ('relative', fm.INVALID_PATH), ('/x/../etc', fm.INVALID_PATH)])
def test_outside_the_roots_refused(owner, tree, p, err):
    r = _get(owner, p)
    assert r.status_code == 400 and r.get_json()['error'] == err


def test_default_roots_are_media_downloads_and_configured(tree, monkeypatch, tmp_path):
    monkeypatch.delenv('LUMA_BROWSE_ROOTS')
    lib = tmp_path / 'lib-elsewhere' / 'Movies'
    lib.mkdir(parents=True)
    tvlib = tmp_path / 'tv-elsewhere'
    tvlib.mkdir()
    fm.save({**fm.load(), 'paths': [{'id': 'p_00000001', 'name': 'M', 'path': str(lib)}]})
    tv_settings.save({'paths': [{'id': 't_00000001', 'name': 'T', 'path': str(tvlib)}]})
    monkeypatch.setitem(fm._cfg, 'other_paths', tv_settings.tv_paths)
    roots = fm.browse_roots()
    expect = {os.path.realpath(fm.downloads_root()), str(lib), str(tvlib)}
    if os.path.isdir('/media') and not fm.blocked('/media'):
        expect.add(os.path.realpath('/media'))
    assert set(roots) == expect
    assert not any(r == '/mnt' or r.startswith('/mnt/') for r in roots if '/mnt' not in expect)


def test_nested_roots_fold(tree, monkeypatch):
    monkeypatch.setenv('LUMA_BROWSE_ROOTS', f'{tree}:{tree / "Video"},{tree / "missing"}')
    assert str(tree / 'Video') not in fm.browse_roots() and str(tree) in fm.browse_roots()


def test_no_mnt_hardcoded():
    src = (ROOT / 'app' / 'file_management.py').read_text()
    assert "'/mnt" not in src and '"/mnt' not in src


def test_browse_never_writes(owner, tree):
    def snap():
        return sorted((str(p), p.lstat().st_mtime_ns) for p in tree.rglob('*'))
    before = snap()
    for p in (None, str(tree), str(tree / 'Video')):
        _get(owner, p)
    assert snap() == before


def test_owner_only(minimal_app, tree):
    _client(minimal_app)
    assert _get(_client(minimal_app, name='second'), str(tree)).status_code == 403
    assert _get(minimal_app.test_client(), str(tree)).status_code == 401
