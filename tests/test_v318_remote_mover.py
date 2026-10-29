"""v318 remote push-import: unix socket host helper; local when remote off."""

from __future__ import annotations

import json
import os
import re
from unittest import mock

import pytest

from app import remote_mover as rm


@pytest.fixture
def remote_env(tmp_path, monkeypatch):
    dl = tmp_path / 'storage' / 'downloads'
    media = tmp_path / 'media'
    sock = tmp_path / 'mover.sock'
    dl.mkdir(parents=True)
    media.mkdir(parents=True)
    monkeypatch.setenv('LUMA_REMOTE_MOVER', '1')
    monkeypatch.setenv('LUMA_REMOTE_MOVER_SOCK', str(sock))
    monkeypatch.setenv('LUMA_REMOTE_DOWNLOADS_ROOT', '/scratch/downloads')
    monkeypatch.setenv('LUMA_REMOTE_MEDIA_ROOT', '/media')
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(dl))
    monkeypatch.delenv('LUMA_INSTANCE', raising=False)
    return {'dl': dl, 'media': media, 'sock': sock}


def test_enabled_and_default_sock(monkeypatch):
    monkeypatch.delenv('LUMA_REMOTE_MOVER', raising=False)
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    assert rm.enabled() is True
    monkeypatch.delenv('LUMA_REMOTE_MOVER_SOCK', raising=False)
    assert rm.resolve_sock() == '/run/luma-mover/mover.sock'
    assert rm.config()['sock'] == '/run/luma-mover/mover.sock'
    assert rm.config()['downloads_root'] == '/scratch/downloads'
    monkeypatch.setenv('LUMA_REMOTE_MOVER_SOCK', '/custom/mover.sock')
    assert rm.resolve_sock() == '/custom/mover.sock'


def test_to_remote_maps(remote_env):
    dl = remote_env['dl']
    src = dl / 'Movie.mkv'
    src.write_bytes(b'abc')
    assert rm.to_remote(str(src)) == '/scratch/downloads/Movie.mkv'
    assert rm.to_remote('/storage/downloads/X/a.mkv') == '/scratch/downloads/X/a.mkv'
    assert rm.to_remote('/media/Video/Movies/A/A.mkv') == '/media/Video/Movies/A/A.mkv'
    with pytest.raises(rm.RemoteMoverError, match='not under'):
        rm.to_remote('/etc/passwd')


def test_transfer_posts_mode_over_unix(remote_env, monkeypatch):
    dl = remote_env['dl']
    src = dl / 'a.mkv'
    src.write_bytes(b'x')
    seen = {}

    def fake_post(sock_path, path, body, timeout):
        seen['sock'] = sock_path
        seen['path'] = path
        seen['body'] = json.loads(body.decode())
        seen['timeout'] = timeout
        return 200, json.dumps({'ok': True, 'rc': 0, 'output': ''})

    monkeypatch.setattr(rm, '_http_post_unix', fake_post)
    assert rm.transfer(str(src), '/media/Movies/a.mkv') == 'copied'
    assert seen['sock'] == str(remote_env['sock'])
    assert seen['path'] == '/move'
    tmp = seen['body'].pop('tmp')   # v324: Luma-chosen temp, same dir as dest
    assert re.fullmatch(r'/media/Movies/\.luma-[0-9a-f]{16}\.luma-partial', tmp)
    assert seen['body'] == {
        'mode': 'cp',
        'src': '/scratch/downloads/a.mkv',
        'dest': '/media/Movies/a.mkv',
    }
    assert 'op' not in seen['body']


def test_transfer_ok_false_raises(remote_env, monkeypatch):
    dl = remote_env['dl']
    (dl / 'a.mkv').write_bytes(b'x')
    monkeypatch.setattr(
        rm, '_http_post_unix',
        lambda *a, **k: (500, json.dumps({'ok': False, 'rc': 1, 'output': 'Permission denied'})))
    with pytest.raises(rm.RemoteMoverError, match='Permission denied'):
        rm.transfer(str(dl / 'a.mkv'), '/media/Movies/a.mkv')


def test_transfer_rc17_exists(remote_env, monkeypatch):
    dl = remote_env['dl']
    (dl / 'a.mkv').write_bytes(b'x')
    monkeypatch.setattr(
        rm, '_http_post_unix',
        lambda *a, **k: (200, json.dumps({'ok': False, 'rc': 17, 'output': 'exists'})))
    assert rm.transfer(str(dl / 'a.mkv'), '/media/Movies/a.mkv') == 'exists'


def test_transfer_rejects_bad_bounds(remote_env, monkeypatch):
    monkeypatch.setattr(rm, 'to_remote', lambda p, cfg=None: '/other/x' if 'mkv' in p else '/media/Y')
    with pytest.raises(rm.RemoteMoverError, match='under /scratch/downloads'):
        rm.transfer('/storage/downloads/a.mkv', '/media/Y/a.mkv')


def test_health_get(remote_env, monkeypatch):
    class FakeConn:
        def __init__(self, *a, **k):
            pass
        def request(self, method, path, headers=None):
            assert method == 'GET'
            assert path == '/health'
        def getresponse(self):
            class R:
                status = 200
                def read(self):
                    return b'{"ok":true}'
            return R()
        def close(self):
            pass
    monkeypatch.setattr(rm, '_UnixHTTPConnection', FakeConn)
    assert rm.health() is True


def test_no_docker_gw_in_module():
    src = open(rm.__file__).read()
    assert '172.17.0.1' not in src
    assert '18772' not in src or 'HOST ONLY' in open(
        os.path.join(os.path.dirname(rm.__file__), '..', 'scripts', 'luma-remote-mover-host.py')
    ).read()


def test_copy_verified_local_when_remote_off(tmp_path, monkeypatch):
    monkeypatch.setenv('LUMA_REMOTE_MOVER', '0')
    monkeypatch.delenv('LUMA_INSTANCE', raising=False)
    from app.movie_import import copy_verified
    src = tmp_path / 's.mkv'
    dest = tmp_path / 'd.mkv'
    src.write_bytes(b'hello-luma')
    assert copy_verified(str(src), str(dest)) == 'copied'
    assert dest.read_bytes() == b'hello-luma'


def test_copy_verified_and_library_mover_remote(remote_env, monkeypatch):
    from app import movie_import as mi, library_mover as lm
    dl = remote_env['dl']
    src = dl / 's.mkv'
    src.write_bytes(b'data')
    seen = {}
    def _fake(s, d, mode='cp'):
        seen['m'] = (s, d, mode)
        return 'copied'
    monkeypatch.setattr(rm, 'transfer', _fake)
    out = str(remote_env['media'] / 'out.mkv')
    assert mi.copy_verified(str(src), out) == 'copied'
    folder = remote_env['media'] / 'Show'
    folder.mkdir()
    dfd = os.open(str(folder), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        how = lm.transfer(str(src), dfd, str(folder), 'ep.mkv', 'copy', None, str(dl))
    finally:
        os.close(dfd)
    assert how == 'copied'
