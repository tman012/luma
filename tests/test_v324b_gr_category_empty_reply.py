"""v324b: movie grabs (watchlist + Genre Roulette) use the test tag (luma-test), never a media_category;
an empty reply / reset from the mover helper is handled like a timeout."""
from __future__ import annotations

import ast
import http.client
import json
import os
import socket
import threading

import pytest

from app import movie_scanner, qbit_owner
from app import remote_mover as rm

APP = os.path.join(os.path.dirname(movie_scanner.__file__))


def test_movie_category_is_test_tag(monkeypatch):
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    monkeypatch.setenv('LUMA_MOVIE_CATEGORY', 'movie')
    assert movie_scanner.category() == 'luma-test' == qbit_owner.tag_name()
    monkeypatch.setenv('LUMA_TEST_QBIT_TAG', 'luma-test-2')
    assert movie_scanner.category() == 'luma-test-2'


def test_grab_fields_carry_test_tag(monkeypatch):
    from app import tv_grab
    monkeypatch.delenv('LUMA_TEST_QBIT_TAG', raising=False)
    f = tv_grab._fields(movie_scanner.category())
    assert f['category'] == 'luma-test' and f['tags'] == 'luma-test'


QBIT_SINKS = ('ensure_category', '_fields', 'createCategory', 'scoped_add')


def test_no_media_category_used_as_qbit_category():
    """No grab path may feed a library type's media_category ('movie'/'tv') to qBittorrent."""
    bad = []
    for dirpath, _, files in os.walk(APP):
        for fn in files:
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dirpath, fn)
            src = open(path, encoding='utf-8').read()
            if 'media_category' not in src:
                continue
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn_name = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)
                    if fn_name in QBIT_SINKS and 'media_category' in ast.unparse(node):
                        bad.append(f'{path}:{node.lineno}')
                if isinstance(node, ast.Dict):
                    for k, v in zip(node.keys, node.values):
                        if isinstance(k, ast.Constant) and k.value in ('category', 'tags') and 'media_category' in ast.unparse(v):
                            bad.append(f'{path}:{node.lineno}')
            for line_no, line in enumerate(src.splitlines(), 1):
                if 'media_category' in line and ('torrents/add' in line or 'createCategory' in line):
                    bad.append(f'{path}:{line_no}')
    assert not bad, bad


def _server(tmp_path, behaviour):
    sock_path = str(tmp_path / 'm.sock')
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)

    def run():
        c, _ = srv.accept()
        c.recv(65536)
        behaviour(c)
        try:
            c.close()
        except OSError:
            pass
        srv.close()
    threading.Thread(target=run, daemon=True).start()
    return sock_path



@pytest.mark.parametrize('behaviour,exc', [
    (lambda c: None, http.client.RemoteDisconnected),                 # empty reply, no status line
    (lambda c: c.sendall(b'garbage\r\n\r\n'), http.client.BadStatusLine),
])
def test_empty_reply_raises_keep_waiting(tmp_path, behaviour, exc):
    sock = _server(tmp_path, behaviour)
    with pytest.raises(rm._HelperStoppedWaiting) as ei:
        rm._http_post_unix(sock, '/move', b'{}', 5)
    assert isinstance(ei.value.__cause__, (exc, ConnectionResetError))   # close may race to a reset


@pytest.mark.parametrize('exc', [ConnectionResetError, BrokenPipeError, ConnectionAbortedError,
                                 http.client.RemoteDisconnected, http.client.BadStatusLine])
def test_reset_family_after_connect_keeps_waiting(tmp_path, monkeypatch, exc):
    class C(rm._UnixHTTPConnection):
        def connect(self):
            self.sock = object()

        def request(self, *a, **k):
            raise exc('drop') if exc is not http.client.RemoteDisconnected else exc('Remote end closed')

        def close(self):
            pass
    monkeypatch.setattr(rm, '_UnixHTTPConnection', C)
    with pytest.raises(rm._HelperStoppedWaiting):
        rm._http_post_unix('/x.sock', '/move', b'{}', 5)


def test_refused_before_connect_is_error(tmp_path):
    p = tmp_path / 'dead.sock'
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(p))
    s.close()                                 # bound, not listening: ECONNREFUSED
    with pytest.raises(rm.RemoteMoverError, match='unreachable'):
        rm._http_post_unix(str(p), '/move', b'{}', 5)


def test_helper_restart_empty_reply_then_copy_lands(tmp_path, monkeypatch):
    """Ops test: KillMode=process restart -> empty reply ~12 s in; cp on .72 finishes and lands."""
    dl = tmp_path / 'dl'
    dl.mkdir()
    dest_dir = tmp_path / 'media' / 'Video' / 'Movies' / 'Max Payne (2008)'
    dest_dir.mkdir(parents=True)
    src = dl / 'Max.Payne.mkv'
    src.write_bytes(b'y' * 4096)
    dest = dest_dir / 'Max Payne (2008).mkv'
    sock = _server(tmp_path, lambda c: None)
    monkeypatch.setenv('LUMA_REMOTE_MOVER', '1')
    monkeypatch.setenv('LUMA_REMOTE_MOVER_SOCK', sock)
    remote = {str(src): '/scratch/downloads/Max.Payne.mkv', str(dest): '/media/Video/Movies/Max Payne (2008)/Max Payne (2008).mkv'}
    monkeypatch.setattr(rm, 'to_remote', lambda p, cfg=None: remote[p])
    clock = {'t': 0.0}
    monkeypatch.setattr(rm, '_clock', lambda: clock['t'])
    polls = []

    def sleep(s):
        clock['t'] += s
        polls.append(clock['t'])
        if clock['t'] >= 45:
            dest.write_bytes(b'y' * 4096)
    monkeypatch.setattr(rm, '_sleep', sleep)
    assert rm.transfer(str(src), str(dest)) == 'copied'
    assert polls and all(p % 15 == 0 for p in polls)


def test_helper_restart_and_copy_really_stopped_fails(tmp_path, monkeypatch):
    dl = tmp_path / 'dl'
    dl.mkdir()
    dest_dir = tmp_path / 'media' / 'M'
    dest_dir.mkdir(parents=True)
    src = dl / 'a.mkv'
    src.write_bytes(b'y' * 10)
    dest = dest_dir / 'a.mkv'
    monkeypatch.setenv('LUMA_REMOTE_MOVER', '1')
    monkeypatch.setenv('LUMA_REMOTE_MOVER_SOCK', _server(tmp_path, lambda c: None))
    monkeypatch.setattr(rm, 'to_remote', lambda p, cfg=None: {str(src): '/scratch/downloads/a.mkv', str(dest): '/media/M/a.mkv'}[p])
    clock = {'t': 0.0}
    monkeypatch.setattr(rm, '_clock', lambda: clock['t'])
    monkeypatch.setattr(rm, '_sleep', lambda s: clock.__setitem__('t', clock['t'] + s))
    with pytest.raises(rm.RemoteMoverError, match='no progress'):
        rm.transfer(str(src), str(dest))
