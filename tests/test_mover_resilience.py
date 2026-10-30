"""Mover resilience: a cut-off remote copy (stall, helper SSH rc 255, refused, timed out, remote host
rebooted) is retried with backoff (REMOTE_TRIES in all) and then fails; a remote copy is recorded on its
job before it starts, so a restart adopts a copy still running instead of starting a second one; and when
partial cleanup removes the partial of an unfinished copy, its job goes back to waiting."""
import json
import os

import pytest

from app import library_mover as lm, partial_cleanup as pc, remote_mover as rm
from tests.test_library_mover import H1, TMDB, add_path, env, job, movie_file, torrent  # noqa: F401
from tests.test_v318_remote_mover import remote_env  # noqa: F401

NOW = 1_000_000.0


# ------------------------------------------------------------------ remote_mover: what counts as transient

def _post(status, rc, output):
    return lambda *a, **k: (status, json.dumps({'ok': False, 'rc': rc, 'output': output}))


@pytest.mark.parametrize('status,rc,out', [
    (500, 255, 'ssh: connect to host 10.0.0.72 port 22: Connection refused'),
    (500, 255, ''),
    (500, 1, 'ssh: connect to host remote: Connection timed out'),
    (500, 1, 'client_loop: send disconnect: Broken pipe'),
])
def test_helper_connection_failures_are_transient(remote_env, monkeypatch, status, rc, out):  # noqa: F811
    (remote_env['dl'] / 'a.mkv').write_bytes(b'x')
    monkeypatch.setattr(rm, '_http_post_unix', _post(status, rc, out))
    monkeypatch.setattr(rm, '_dest_observable', lambda d: True)
    monkeypatch.setattr(rm, 'wait_for_remote_copy', lambda *a: pytest.fail('rc 255 must not wait'))
    with pytest.raises(rm.RemoteTransient):
        rm.transfer(str(remote_env['dl'] / 'a.mkv'), '/media/Movies/a.mkv')


def test_a_script_refusal_is_not_transient(remote_env, monkeypatch):  # noqa: F811
    (remote_env['dl'] / 'a.mkv').write_bytes(b'x')
    monkeypatch.setattr(rm, '_http_post_unix', _post(500, 3, 'src missing'))
    with pytest.raises(rm.RemoteMoverError) as ei:
        rm.transfer(str(remote_env['dl'] / 'a.mkv'), '/media/Movies/a.mkv')
    assert not isinstance(ei.value, rm.RemoteTransient)


def test_unreachable_socket_is_transient(remote_env, monkeypatch):  # noqa: F811
    (remote_env['dl'] / 'a.mkv').write_bytes(b'x')
    with pytest.raises(rm.RemoteTransient):     # nothing listens on remote_env['sock']
        rm.transfer(str(remote_env['dl'] / 'a.mkv'), '/media/Movies/a.mkv')


def test_a_stall_is_transient(tmp_path, monkeypatch):
    src, dest, tmp = tmp_path / 's.mkv', tmp_path / 'd.mkv', tmp_path / '.luma-0123456789abcdef.luma-partial'
    src.write_bytes(b'x' * 100)
    tmp.write_bytes(b'x' * 10)                  # never grows: the remote host went away
    t = [0.0]
    monkeypatch.setattr(rm, '_now', lambda: t[0])
    monkeypatch.setattr(rm, '_sleep', lambda s: t.__setitem__(0, t[0] + s))
    with pytest.raises(rm.RemoteTransient, match='stalled'):
        rm.wait_for_remote_copy(str(src), str(dest), str(tmp))


def test_partial_alive(tmp_path):
    p = tmp_path / '.luma-0123456789abcdef.luma-partial'
    assert not rm.partial_alive(str(p))
    p.write_bytes(b'x')
    st = os.stat(p)
    assert rm.partial_alive(str(p), now=st.st_mtime + 1)
    assert not rm.partial_alive(str(p), now=st.st_mtime + rm.STALL_S + 1)


# ------------------------------------------------------------------ library_mover with a fake remote host

class Remote:
    """The remote host: transfer() writes the partial then plays out the next scripted outcome."""

    def __init__(self, monkeypatch):
        self.plan, self.calls, self.adopted, self.alive = [], [], [], True
        monkeypatch.setenv('LUMA_REMOTE_MOVER', '1')
        monkeypatch.setattr(rm, 'transfer', self.transfer)
        monkeypatch.setattr(rm, 'adopt', self.adopt)
        monkeypatch.setattr(rm, 'mkdir_p', lambda d, cfg=None: None)
        monkeypatch.setattr(rm, 'partial_alive', lambda p, now=None: self.alive and os.path.exists(p))

    def transfer(self, src, dest, mode='cp', tmp_container=None):
        assert tmp_container and os.path.basename(tmp_container).endswith('.luma-partial')
        self.calls.append(tmp_container)
        with open(tmp_container, 'wb') as f:
            f.write(b'part')
        step = self.plan.pop(0) if self.plan else 'ok'
        if step == 'ok':
            os.rename(tmp_container, dest)
            return 'copied'
        if isinstance(step, BaseException):
            raise step
        raise rm.RemoteTransient(step)

    def adopt(self, src, dest, tmp):
        self.adopted.append(tmp)
        os.rename(tmp, dest)
        return 'copied'


@pytest.fixture
def remote(env, monkeypatch):  # noqa: F811
    add_path('Movies', env.media / 'Movies')
    movie_file(env, 'Heat.1995.1080p/Heat.1995.1080p.mkv')
    torrent(env, H1, 'Heat.1995.1080p')
    lm.track(H1, TMDB, 'Heat.1995.1080p')
    return Remote(monkeypatch)


def _final(env):
    return env.media / 'Movies' / 'Heat (1995)' / 'Heat (1995).mkv'


def test_reboot_mid_copy_rc255_then_a_successful_retry(env, remote):  # noqa: F811
    remote.plan = ['host mover connection failed HTTP 500 rc=255: Connection closed by remote host']
    env.tick(NOW)
    j = job(H1)
    assert j['state'] == 'waiting' and j['remote_tries'] == 1 and j['retry_at'] == NOW + lm.REMOTE_RETRY_S[0]
    assert 'inflight' not in j and len(j['cut_partials']) == 1
    env.tick(NOW + 10)                          # still backing off: the helper isn't asked
    assert len(remote.calls) == 1
    env.tick(NOW + lm.REMOTE_RETRY_S[0])
    j = job(H1)
    assert j['state'] == 'done' and j['remote_tries'] == 0 and j['retry_at'] is None
    assert len(remote.calls) == 2 and remote.calls[0] != remote.calls[1] and remote.adopted == []
    assert _final(env).read_bytes() == b'part'


def test_cut_off_copies_fail_after_the_last_try(env, remote):  # noqa: F811
    remote.plan = ['remote copy stalled at 4 B'] * lm.REMOTE_TRIES
    t = NOW
    for i in range(lm.REMOTE_TRIES):
        env.tick(t)
        if i < len(lm.REMOTE_RETRY_S):
            assert job(H1)['state'] == 'waiting'
            t += lm.REMOTE_RETRY_S[i]
    j = job(H1)
    assert j['state'] == 'failed' and 'stalled' in j['error'] and j['remote_tries'] == lm.REMOTE_TRIES
    assert len(remote.calls) == lm.REMOTE_TRIES


def test_a_refusal_is_still_skipped(env, remote, monkeypatch):  # noqa: F811
    def refuse(*a, **k):
        raise rm.RemoteMoverError('remote SRC must be under /scratch')
    monkeypatch.setattr(rm, 'transfer', refuse)
    env.tick(NOW)
    assert job(H1)['state'] == 'skipped' and 'inflight' not in job(H1)


def test_restart_mid_copy_adopts_the_running_copy(env, remote):  # noqa: F811
    remote.plan = [SystemExit('container recreated')]
    with pytest.raises(SystemExit):
        env.tick(NOW)
    j = job(H1)
    assert j['state'] == 'waiting' and list(j['inflight'].values()) == remote.calls
    lm._current['h'] = None                     # a new process
    env.tick(NOW + 60)
    assert len(remote.calls) == 1, 'a second copy was started'
    assert remote.adopted == remote.calls
    j = job(H1)
    assert j['state'] == 'done' and 'inflight' not in j
    assert _final(env).exists()


def test_restart_after_the_copy_died_starts_one_fresh_copy(env, remote):  # noqa: F811
    remote.plan = [SystemExit('container recreated')]
    with pytest.raises(SystemExit):
        env.tick(NOW)
    remote.alive = False                        # the old partial stopped changing
    env.tick(NOW + 60)
    assert len(remote.calls) == 2 and remote.adopted == []
    assert job(H1)['state'] == 'done'


def test_restart_after_the_copy_landed_verifies_it(env, remote, monkeypatch):  # noqa: F811
    remote.plan = [SystemExit('container recreated')]
    with pytest.raises(SystemExit):
        env.tick(NOW)
    tmp = remote.calls[0]
    os.rename(tmp, str(_final(env)))            # the remote host finished while Luma was down
    monkeypatch.setattr(rm, 'adopt', lambda s, d, t: (remote.adopted.append(t), 'copied')[1])
    env.tick(NOW + 60)
    assert len(remote.calls) == 1 and remote.adopted == [tmp]
    assert job(H1)['state'] == 'done'


# ------------------------------------------------------------------ partial cleanup requeues the job

def _age(p, s=7200):
    st = os.stat(p)
    os.utime(p, (st.st_atime - s, st.st_mtime - s))


def test_cleanup_of_a_failed_jobs_partial_requeues_it_once(env, remote, monkeypatch):  # noqa: F811
    remote.plan = ['remote copy stalled'] * (2 * lm.REMOTE_TRIES)
    t = NOW
    for w in lm.REMOTE_RETRY_S + (0,):
        env.tick(t)
        t += w
    assert job(H1)['state'] == 'failed'
    for p in remote.calls:
        _age(p)
    monkeypatch.setattr(pc, 'library_roots', lambda: [str(env.media / 'Movies')])
    monkeypatch.setitem(pc._last, 't', 0.0)
    monkeypatch.setattr(pc, 'SETTLE_S', 0)
    monkeypatch.setattr(pc.time, 'sleep', lambda s: None)
    removed = pc.maybe_sweep()
    assert sorted(removed) == sorted(os.path.realpath(p) for p in remote.calls)
    j = job(H1)
    assert j['state'] == 'waiting' and j['cleanup_requeues'] == 1 and 'cut_partials' not in j
    for w in lm.REMOTE_RETRY_S + (0,):          # fails again ...
        env.tick(t)
        t += w
    assert job(H1)['state'] == 'failed'
    for p in remote.calls[lm.REMOTE_TRIES:]:
        _age(p)
    lm.partials_removed(pc.sweep([str(env.media / 'Movies')], settle_s=0))
    assert job(H1)['state'] == 'failed'         # ... and is requeued only once


def test_cleanup_of_an_unfinished_waiting_jobs_partial_keeps_it_waiting(env, remote):  # noqa: F811
    remote.plan = [SystemExit('container recreated')]
    with pytest.raises(SystemExit):
        env.tick(NOW)
    tmp = remote.calls[0]
    _age(tmp)
    assert lm.partials_removed(pc.sweep([str(env.media / 'Movies')], settle_s=0)) == 1
    j = job(H1)
    assert j['state'] == 'waiting' and 'inflight' not in j
    env.tick(NOW + 60)                          # a fresh copy, not an adoption of the removed one
    assert len(remote.calls) == 2 and job(H1)['state'] == 'done'


def test_cleanup_ignores_partials_of_finished_or_other_jobs(env, remote):  # noqa: F811
    env.tick(NOW)
    assert job(H1)['state'] == 'done'
    assert lm.partials_removed([str(env.media / 'Movies' / '.luma-0123456789abcdef.luma-partial')]) == 0
    assert job(H1)['state'] == 'done'
