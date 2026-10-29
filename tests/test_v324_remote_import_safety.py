"""v324: remote hop writes a temp (.luma-<hex16>.luma-partial, full path in dest's folder) then renames;
a helper that stops waiting (timeout / 5xx / rc 124) is followed by watching the files through /media."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import socket

import pytest

from app import library_mover as lm
from app import remote_mover as rm

PART = re.compile(r'^\.luma-[0-9a-f]{16}\.luma-partial$')


@pytest.fixture
def env(tmp_path, monkeypatch):
    dl = tmp_path / 'downloads'
    dl.mkdir()
    dest_dir = tmp_path / 'media' / 'Video' / 'Movies' / 'Smile 2 (2024)'
    dest_dir.mkdir(parents=True)
    src = dl / 'Smile.2.mkv'
    src.write_bytes(b'x' * 1000)
    dest = dest_dir / 'Smile 2 (2024).mkv'
    monkeypatch.setenv('LUMA_REMOTE_MOVER', '1')
    monkeypatch.setenv('LUMA_REMOTE_MOVER_SOCK', str(tmp_path / 's.sock'))
    remote = {str(src): '/scratch/downloads/Smile.2.mkv', str(dest): '/media/Video/Movies/Smile 2 (2024)/Smile 2 (2024).mkv'}
    monkeypatch.setattr(rm, 'to_remote', lambda p, cfg=None: remote[p])
    clock = {'t': 0.0}
    monkeypatch.setattr(rm, '_clock', lambda: clock['t'])
    return {'src': src, 'dest': dest, 'dir': dest_dir, 'clock': clock, 'mp': monkeypatch}


def _post(env, reply, on_sleep=None):
    seen = {}

    def fake(sock, path, body, timeout):
        seen['body'] = json.loads(body)
        if isinstance(reply, BaseException):
            raise reply
        return reply
    env['mp'].setattr(rm, '_http_post_unix', fake)

    def sleep(s):
        env['clock']['t'] += s
        if on_sleep:
            on_sleep(seen, env['clock']['t'])
    env['mp'].setattr(rm, '_sleep', sleep)
    return seen


def _tmp_c(env, seen):
    return env['dir'] / os.path.basename(seen['body']['tmp'])


def test_partial_name_shape_shared():
    n = lm.partial_name()
    assert PART.match(n) and lm.PARTIAL_RE.match(n)
    assert lm.partial_name() != n


def test_local_new_partial_uses_shared_name(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, 'partial_name', lambda: '.luma-0123456789abcdef.luma-partial')
    dfd = os.open(str(tmp_path), os.O_RDONLY)
    try:
        name, fd, _ = lm._new_partial(dfd, 'Some Title (2020).mkv')
        os.close(fd)
    finally:
        os.close(dfd)
    assert name == '.luma-0123456789abcdef.luma-partial'
    assert 'Title' not in name


def test_payload_has_full_tmp_in_dest_folder(env):
    seen = _post(env, (200, json.dumps({'ok': True, 'rc': 0})))
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'
    b = seen['body']
    assert set(b) == {'mode', 'src', 'dest', 'tmp'}
    assert b['mode'] == 'cp'
    assert os.path.dirname(b['tmp']) == os.path.dirname(b['dest'])
    assert PART.match(os.path.basename(b['tmp']))
    assert 'Smile' not in os.path.basename(b['tmp'])


@pytest.mark.parametrize('reply', [
    (500, json.dumps({'ok': False, 'rc': 124, 'output': 'timed out'})),
    (500, json.dumps({'ok': False, 'output': 'gateway timeout'})),
    (200, json.dumps({'ok': False, 'rc': 124})),
    (502, ''),
    (504, '<html>gateway</html>'),
])
def test_helper_stopped_waiting_then_copy_lands(env, reply):
    def land(seen, t):
        tmp = _tmp_c(env, seen)
        if t < 30:
            tmp.write_bytes(b'x' * int(t * 10))
        else:
            tmp.unlink(missing_ok=True)
            env['dest'].write_bytes(b'x' * 1000)
    _post(env, reply, land)
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'


def test_socket_timeout_keeps_waiting(env):
    def land(seen, t):
        env['dest'].write_bytes(b'x' * 1000)
    _post(env, rm._HelperStoppedWaiting('timeout'), land)
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'


@pytest.mark.parametrize('rc', [2, 3, 4, 5, 6])
def test_script_fail_rc_is_immediate(env, rc):
    slept = []
    _post(env, (500, json.dumps({'ok': False, 'rc': rc, 'output': 'bad'})), lambda s, t: slept.append(t))
    with pytest.raises(rm.RemoteMoverError, match=f'rc={rc}'):
        rm.transfer(str(env['src']), str(env['dest']))
    assert slept == []


def test_temp_vanishes_without_final_fails(env):
    def go(seen, t):
        tmp = _tmp_c(env, seen)
        if t <= 15:
            tmp.write_bytes(b'x' * 10)
        else:
            tmp.unlink(missing_ok=True)
    _post(env, (500, json.dumps({'ok': False, 'rc': 124})), go)
    with pytest.raises(rm.RemoteMoverError, match='temp file gone'):
        rm.transfer(str(env['src']), str(env['dest']))


def test_no_growth_fails_after_600s(env):
    def go(seen, t):
        _tmp_c(env, seen).write_bytes(b'x' * 10)
    _post(env, (500, json.dumps({'ok': False, 'rc': 124})), go)
    with pytest.raises(rm.RemoteMoverError, match='no progress'):
        rm.transfer(str(env['src']), str(env['dest']))
    assert 600 <= env['clock']['t'] <= 630


def test_deadline(env):
    assert rm.deadline_s(10) == 1800
    big = 100 * 1024 ** 3
    assert rm.deadline_s(big) == pytest.approx(2 * big / (5 * 1024 * 1024))

    def grow(seen, t):
        _tmp_c(env, seen).write_bytes(b'x' * int(t))
    _post(env, (500, json.dumps({'ok': False, 'rc': 124})), grow)
    with pytest.raises(rm.RemoteMoverError, match='did not finish within 1800'):
        rm.transfer(str(env['src']), str(env['dest']))


def test_wrong_final_size_fails(env):
    def go(seen, t):
        env['dest'].write_bytes(b'x' * 5)
    _post(env, (500, json.dumps({'ok': False, 'rc': 124})), go)
    with pytest.raises(rm.RemoteMoverError, match='wrong size'):
        rm.transfer(str(env['src']), str(env['dest']))


def test_unknown_source_size_needs_tmp_gone_and_stable_final(env):
    env['mp'].setattr(rm, '_size', (lambda orig: (lambda p: None if p == str(env['src']) else orig(p)))(rm._size))
    sizes = iter([100, 200, 300, 300, 300])
    polls = []

    def go(seen, t):
        polls.append(t)
        env['dest'].write_bytes(b'x' * next(sizes))
    _post(env, (500, json.dumps({'ok': False, 'rc': 124})), go)
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'
    assert len(polls) >= 4          # 100, 200, 300, 300 -> stable twice


def test_unknown_source_size_with_tmp_present_is_not_success(env):
    env['mp'].setattr(rm, '_size', (lambda orig: (lambda p: None if p == str(env['src']) else orig(p)))(rm._size))

    def go(seen, t):
        env['dest'].write_bytes(b'x' * 50)
        _tmp_c(env, seen).write_bytes(b'x' * 10)
    _post(env, (500, json.dumps({'ok': False, 'rc': 124})), go)
    with pytest.raises(rm.RemoteMoverError):
        rm.transfer(str(env['src']), str(env['dest']))


def test_socket_timeout_before_send_is_error(tmp_path, monkeypatch):
    class C(rm._UnixHTTPConnection):
        def connect(self):
            raise socket.timeout('connect')
    monkeypatch.setattr(rm, '_UnixHTTPConnection', C)
    with pytest.raises(rm.RemoteMoverError, match='unreachable'):
        rm._http_post_unix(str(tmp_path / 'x.sock'), '/move', b'{}', 5)


def _host():
    p = os.path.join(os.path.dirname(rm.__file__), '..', 'scripts', 'luma-remote-mover-host.py')
    spec = importlib.util.spec_from_file_location('luma_host_v324', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_host_tmp_validation_and_normpath():
    h = _host()
    d = '/media/Video/Movies/A/A.mkv'
    assert h.tmp_ok('/media/Video/Movies/A/.luma-0123456789abcdef.luma-partial', d)
    assert not h.tmp_ok('.luma-0123456789abcdef.luma-partial', d)
    assert not h.tmp_ok('/media/Video/Movies/B/.luma-0123456789abcdef.luma-partial', d)
    assert not h.tmp_ok('/media/Video/Movies/A/A.mkv.luma.partial', d)
    assert not h.tmp_ok('/media/Video/Movies/A/../A/.luma-0123456789abcdef.luma-partial', d)
    assert not h._under('/media/../etc/passwd', '/media')
    assert h._under('/media/./Video', '/media')
    rc, out = h.run_move('cp', '/scratch/downloads/a', d, '/tmp/.luma-0123456789abcdef.luma-partial')
    assert rc == 2


def test_host_passes_tmp_as_4th_arg_and_never_kills(tmp_path, monkeypatch):
    h = _host()
    cli = tmp_path / 'cli.sh'
    log = tmp_path / 'args'
    cli.write_text(f'#!/bin/sh\necho "$@" > {log}\nexit 0\n')
    cli.chmod(0o755)
    monkeypatch.setattr(h, 'CLI', str(cli))
    monkeypatch.setattr(h, 'DOWNLOADS_ROOT', '/scratch/downloads')
    monkeypatch.setattr(h, 'MEDIA_ROOT', '/media')
    tmp = '/media/V/A/.luma-0123456789abcdef.luma-partial'
    assert h.run_move('cp', '/scratch/downloads/a', '/media/V/A/A.mkv', tmp)[0] == 0
    assert log.read_text().split() == ['cp', '/scratch/downloads/a', '/media/V/A/A.mkv', tmp]
    slow = tmp_path / 'slow.sh'
    slow.write_text(f'#!/bin/sh\nsleep 2\ntouch {tmp_path}/done\n')
    slow.chmod(0o755)
    monkeypatch.setattr(h, 'CLI', str(slow))
    monkeypatch.setattr(h, 'TIMEOUT_S', 0.2)
    rc, _ = h.run_move('cp', '/scratch/downloads/a', '/media/V/A/A.mkv', tmp)
    assert rc == 124
    import time
    time.sleep(2.5)
    assert (tmp_path / 'done').exists()     # the copy was left running


def test_5xx_with_other_rc_fails_at_once(env):
    slept = []
    _post(env, (500, json.dumps({'ok': False, 'rc': 1, 'output': 'Permission denied'})), lambda s, t: slept.append(t))
    with pytest.raises(rm.RemoteMoverError, match='Permission denied'):
        rm.transfer(str(env['src']), str(env['dest']))
    assert slept == []
