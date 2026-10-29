"""v324 remote import safety: partial-then-rename owned by .72 script; Luma
sends final name, verifies size via /media, and waits out helper timeouts."""

from __future__ import annotations

import json
import os
import re

import pytest

from app import remote_mover as rm


@pytest.fixture
def env(tmp_path, monkeypatch):
    dl = tmp_path / 'downloads'
    media = tmp_path / 'media'
    dl.mkdir()
    (media / 'Movies' / 'A').mkdir(parents=True)
    monkeypatch.setenv('LUMA_REMOTE_MOVER', '1')
    monkeypatch.setenv('LUMA_REMOTE_MOVER_SOCK', str(tmp_path / 's.sock'))
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(dl))
    monkeypatch.setenv('LUMA_REMOTE_MEDIA_ROOT', '/media')
    real_to_remote = rm.to_remote

    def to_remote(p, cfg=None):
        p = str(p)
        if p.startswith(str(media)):
            return '/media' + p[len(str(media)):]
        return real_to_remote(p, cfg)
    monkeypatch.setattr(rm, 'to_remote', to_remote)
    src = dl / 'a.mkv'
    src.write_bytes(b'x' * 1000)
    dest = media / 'Movies' / 'A' / 'A.mkv'
    clock = {'t': 0.0}
    monkeypatch.setattr(rm, '_now', lambda: clock['t'])
    return {'src': src, 'dest': dest, 'clock': clock, 'mp': monkeypatch}


class FakeScript:
    """Simulates .72 script: writes <dest>.luma.partial then renames."""

    def __init__(self, e, steps, resp=(200, {'ok': True, 'rc': 0, 'output': 'OK'})):
        self.e, self.steps, self.resp, self.bodies = e, list(steps), resp, []
        self.part = None

    def post(self, sock, path, body, timeout):
        b = json.loads(body)
        self.bodies.append((b, timeout))
        self.part = str(self.e['dest'].parent / os.path.basename(b['tmp']))
        self.advance()
        st, d = self.resp
        return st, json.dumps(d)

    def advance(self):
        if not self.steps:
            return
        kind, n = self.steps.pop(0)
        import os
        if kind == 'part':
            open(self.part, 'wb').write(b'x' * n)
        elif kind == 'final':
            if os.path.exists(self.part):
                os.remove(self.part)
            open(self.e['dest'], 'wb').write(b'x' * n)
        elif kind == 'gone':
            if os.path.exists(self.part):
                os.remove(self.part)

    def sleep(self, s):
        self.e['clock']['t'] += s
        self.advance()


def _install(e, fs):
    e['mp'].setattr(rm, '_http_post_unix', fs.post)
    e['mp'].setattr(rm, '_sleep', fs.sleep)


def test_sends_final_name_partial_then_rename(env):
    fs = FakeScript(env, [('part', 400), ('final', 1000)])
    _install(env, fs)
    # helper returns OK only after rename: simulate by finishing in post
    fs.steps = [('final', 1000)]
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'
    body = fs.bodies[0][0]
    assert body['dest'] == '/media/Movies/A/A.mkv'
    assert not body['dest'].endswith(('.luma.partial', '.luma-partial'))
    assert env['dest'].stat().st_size == 1000
    assert list(p.name for p in env['dest'].parent.iterdir()) == ['A.mkv']


def test_no_double_suffix_refuses_partial_dest(env):
    fs = FakeScript(env, [])
    _install(env, fs)
    for suf in ('.luma.partial', '.luma-partial', '.lumapart'):
        with pytest.raises(rm.RemoteMoverError, match='final name'):
            rm.transfer(str(env['src']), str(env['dest']) + suf)
    assert fs.bodies == []
    assert [p.name for p in env['dest'].parent.iterdir()] == []


def test_size_mismatch_fails(env):
    fs = FakeScript(env, [('final', 1500)])
    _install(env, fs)
    with pytest.raises(rm.RemoteMoverError, match='size mismatch'):
        rm.transfer(str(env['src']), str(env['dest']))


def test_script_size_mismatch_rc6_no_final_no_wait(env):
    fs = FakeScript(env, [('gone', 0)],
                    resp=(500, {'ok': False, 'rc': 6, 'output': 'ERROR: size mismatch src=1000 partial=10'}))
    _install(env, fs)
    with pytest.raises(rm.RemoteMoverError, match='size mismatch'):
        rm.transfer(str(env['src']), str(env['dest']))
    assert not env['dest'].exists()
    assert env['clock']['t'] == 0.0   # reported immediately, no polling


def test_copy_past_600s_helper_timeout_not_failed(env):
    # helper times out at 600s (rc 124 / HTTP 500) while partial keeps growing
    steps = [('part', 100)] + [('part', 100 + 20 * i) for i in range(1, 60)] + [('final', 1000)]
    fs = FakeScript(env, steps, resp=(500, {'ok': False, 'rc': 124, 'output': 'timed out after 600s'}))
    _install(env, fs)
    env['mp'].setattr(rm, 'MIN_WAIT_S', 3600.0)
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'
    assert env['clock']['t'] > 600
    assert env['dest'].stat().st_size == 1000


def test_plain_500_non_script_rc_waits(env):
    fs = FakeScript(env, [('part', 10), ('part', 500), ('final', 1000)],
                    resp=(500, {'ok': False, 'rc': 1, 'output': 'Internal Server Error'}))
    _install(env, fs)
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'


def test_socket_timeout_waits(env):
    fs = FakeScript(env, [('part', 10), ('final', 1000)])
    from app import library_mover as lm
    env['mp'].setattr(lm, 'partial_name', lambda: '.luma-00000000000000aa.luma-partial')
    fs.part = str(env['dest'].parent / '.luma-00000000000000aa.luma-partial')

    def post(sock, path, body, timeout):
        import socket as _s
        try:
            raise _s.timeout('timed out')
        except _s.timeout as e:
            raise rm.RemoteMoverError('mover socket unreachable') from e
    env['mp'].setattr(rm, '_http_post_unix', post)
    env['mp'].setattr(rm, '_sleep', fs.sleep)
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'


def test_real_failure_partial_removed_reported(env):
    fs = FakeScript(env, [('part', 300), ('gone', 0)],
                    resp=(500, {'ok': False, 'rc': 124, 'output': 'timed out after 600s'}))
    _install(env, fs)
    with pytest.raises(rm.RemoteMoverError, match='partial removed'):
        rm.transfer(str(env['src']), str(env['dest']))
    assert not env['dest'].exists()


def test_stall_reported(env):
    fs = FakeScript(env, [('part', 300)],
                    resp=(500, {'ok': False, 'rc': 124, 'output': 'timed out'}))
    _install(env, fs)
    with pytest.raises(rm.RemoteMoverError, match='stalled'):
        rm.transfer(str(env['src']), str(env['dest']))
    assert not env['dest'].exists()


def test_explicit_script_errors_reported(env):
    for rc in (3, 4, 5):
        fs = FakeScript(env, [], resp=(500, {'ok': False, 'rc': rc, 'output': 'ERROR: x'}))
        _install(env, fs)
        with pytest.raises(rm.RemoteMoverError, match='host mover failed'):
            rm.transfer(str(env['src']), str(env['dest']))


def test_socket_timeout_is_size_based(env):
    fs = FakeScript(env, [('final', 1000)])
    _install(env, fs)
    env['mp'].setattr(rm, '_size_of', lambda p: 40 * 1024 ** 3 if p == str(env['src']) else 1000 if p == str(env['dest']) else None)
    env['mp'].setattr(rm, '_verify_final', lambda s, d, t: None)
    rm.transfer(str(env['src']), str(env['dest']))
    assert fs.bodies[0][1] >= 2 * 40 * 1024 ** 3 / rm.MIN_RATE_BPS


def test_remote_temp_name_matches_local_helper_no_title(env, monkeypatch):
    from app import library_mover as lm
    import secrets
    monkeypatch.setattr(secrets, 'token_hex', lambda n: 'ab' * n)
    expected = lm.partial_name()
    assert expected == '.luma-' + 'ab' * 8 + lm.PARTIAL_SUFFIX
    fs = FakeScript(env, [('final', 1000)])
    _install(env, fs)
    assert rm.transfer(str(env['src']), str(env['dest'])) == 'copied'
    body = fs.bodies[0][0]
    assert body['tmp'] == '/media/Movies/A/' + expected      # same folder as dest
    assert os.path.dirname(body['tmp']) == os.path.dirname(body['dest'])
    name = os.path.basename(body['tmp'])
    assert 'A.mkv' not in name and 'Movies' not in name
    assert re.fullmatch(r'\.luma-[0-9a-f]{16}\.luma-partial', name)
    assert not name.endswith('.luma-partial.luma-partial')


def test_local_new_partial_uses_same_helper(tmp_path, monkeypatch):
    from app import library_mover as lm
    monkeypatch.setattr(lm, 'partial_name', lambda: '.luma-0123456789abcdef.luma-partial')
    dfd = os.open(str(tmp_path), os.O_RDONLY)
    try:
        tmp, fd, _ = lm._new_partial(dfd, 'Some Title (2024).mkv')
        os.close(fd)
    finally:
        os.close(dfd)
    assert tmp == '.luma-0123456789abcdef.luma-partial'
