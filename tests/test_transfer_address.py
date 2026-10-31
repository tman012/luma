"""Transfer address: optional second library-server address for the download-host -> library
copy (remote push-import / mover). Blank keeps the old path, set sends it (format-only save), a
failed copy through it falls back to the main address with a log line, Plex never reads it."""

from __future__ import annotations

import json
import logging
import pathlib
import re

import pytest

from app import file_management as fm
from app import remote_mover as rm
from tests.test_file_management import HDR, MAPPING, REMOTE, _client, minimal, store, media  # noqa: F401


@pytest.fixture
def remote_env(tmp_path, monkeypatch, store):  # noqa: F811
    dl = tmp_path / 'storage' / 'downloads'
    dl.mkdir(parents=True)
    monkeypatch.setenv('LUMA_REMOTE_MOVER', '1')
    monkeypatch.setenv('LUMA_REMOTE_MOVER_SOCK', str(tmp_path / 'mover.sock'))
    monkeypatch.setenv('LUMA_REMOTE_DOWNLOADS_ROOT', '/scratch/downloads')
    monkeypatch.setenv('LUMA_REMOTE_MEDIA_ROOT', '/media')
    monkeypatch.setenv('LUMA_DOWNLOADS_ROOT', str(dl))
    src = dl / 'a.mkv'
    src.write_bytes(b'x')
    return src


def _capture(monkeypatch, replies=None):
    calls = []

    def fake_post(sock_path, path, body, timeout):
        calls.append(json.loads(body.decode()))
        if replies:
            return replies.pop(0)
        return 200, json.dumps({'ok': True, 'rc': 0, 'output': ''})

    monkeypatch.setattr(rm, '_http_post_unix', fake_post)
    return calls


def _set_address(addr):
    fm.update(lambda d: d.__setitem__('transfer_address', addr))


# ------------------------------------------------------------------ store

def test_missing_key_reads_blank(store):  # noqa: F811
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({'version': 1, 'paths': [], 'transfer': 'copy'}))
    assert fm.load()['transfer_address'] == ''
    assert fm.transfer_address() == ''
    assert fm.view()['transfer_address'] == ''


def test_valid_address():
    for ok in ('10.10.10.25', 'nas', 'nas.local', 'fd00::25', 'library-1.lan'):
        assert fm.valid_address(ok), ok
    for bad in ('', 'http://10.0.0.1', '10.0.0.1:22', '10.0.0', 'a b', '/media', '-x', '999.1.1.1'):
        assert not fm.valid_address(bad), bad


# ------------------------------------------------------------------ runtime copy path

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(rm, '_transfer_clock', clock)
    monkeypatch.setattr(rm, '_transfer_cooldown_until', {})
    monkeypatch.setattr(rm, '_transfer_logged_at', {})
    return clock


FAIL = (500, json.dumps({'ok': False, 'rc': 5, 'output': 'copy failed'}))
OK = (200, json.dumps({'ok': True, 'rc': 0, 'output': ''}))


def _fallback_logs(caplog, addr):
    return [r for r in caplog.records if f'via transfer address {addr} failed' in r.getMessage()]


def test_constants():
    assert rm.TRANSFER_COOLDOWN_S == 15 * 60
    assert rm.TRANSFER_LOG_INTERVAL_S == 60 * 60
    assert not hasattr(fm, 'address_reachable') and not hasattr(fm, 'UNREACHABLE_ADDRESS')


def test_blank_keeps_old_body(remote_env, monkeypatch):
    calls = _capture(monkeypatch)
    assert rm.transfer(str(remote_env), '/media/Movies/a.mkv') == 'copied'
    assert len(calls) == 1
    body = calls[0]
    assert re.fullmatch(r'/media/Movies/\.luma-[0-9a-f]{16}\.luma-partial', body.pop('tmp'))
    assert body == {'mode': 'cp', 'src': '/scratch/downloads/a.mkv', 'dest': '/media/Movies/a.mkv'}


def test_set_uses_transfer_address_without_probe(remote_env, monkeypatch):
    _set_address('10.10.10.25')
    from app import adb_utils
    monkeypatch.setattr(adb_utils, 'tcp_probe', lambda *a, **k: pytest.fail('no pre-probe'))
    calls = _capture(monkeypatch)
    assert rm.transfer(str(remote_env), '/media/Movies/a.mkv') == 'copied'
    assert len(calls) == 1 and calls[0]['transfer_host'] == '10.10.10.25'
    assert calls[0]['src'] == '/scratch/downloads/a.mkv' and calls[0]['dest'] == '/media/Movies/a.mkv'


def test_failed_copy_falls_back_once_and_logs(remote_env, monkeypatch, caplog):
    _set_address('nas-10g')
    calls = _capture(monkeypatch, replies=[FAIL, OK])
    with caplog.at_level(logging.WARNING, logger='app.remote_mover'):
        assert rm.transfer(str(remote_env), '/media/Movies/a.mkv') == 'copied'
    assert [c.get('transfer_host') for c in calls] == ['nas-10g', None]
    assert calls[0]['tmp'] == calls[1]['tmp']
    assert len(_fallback_logs(caplog, 'nas-10g')) == 1


def test_main_failure_after_fallback_is_not_retried_again(remote_env, monkeypatch):
    _set_address('nas-10g')
    calls = _capture(monkeypatch, replies=[FAIL, FAIL])
    with pytest.raises(rm.RemoteMoverError):
        rm.transfer(str(remote_env), '/media/Movies/a.mkv')
    assert [c.get('transfer_host') for c in calls] == ['nas-10g', None]


def test_transient_failure_is_not_retried_on_main(remote_env, monkeypatch):
    _set_address('nas-10g')
    monkeypatch.setattr(rm, '_dest_observable', lambda d: False)
    calls = _capture(monkeypatch, replies=[(500, json.dumps({'ok': False, 'rc': 255, 'output': 'ssh: connection refused'}))])
    with pytest.raises(rm.RemoteTransient):
        rm.transfer(str(remote_env), '/media/Movies/a.mkv')
    assert len(calls) == 1
    assert rm._transfer_cooldown_until == {}   # not a fallback: no cooldown


def test_cooldown_skips_then_resumes(remote_env, monkeypatch, fresh_state):
    clock = fresh_state
    _set_address('nas-10g')
    calls = _capture(monkeypatch, replies=[FAIL, OK, OK, OK, OK])
    rm.transfer(str(remote_env), '/media/Movies/a.mkv')            # fallback -> cooldown starts
    clock.t += rm.TRANSFER_COOLDOWN_S - 1
    rm.transfer(str(remote_env), '/media/Movies/a.mkv')            # still cooling: main address
    clock.t += 1
    rm.transfer(str(remote_env), '/media/Movies/a.mkv')            # 15 min up: transfer address again
    assert [c.get('transfer_host') for c in calls] == ['nas-10g', None, None, 'nas-10g']


def test_cooldown_is_per_address(remote_env, monkeypatch):
    _set_address('nas-a')
    calls = _capture(monkeypatch, replies=[FAIL, OK, OK])
    rm.transfer(str(remote_env), '/media/Movies/a.mkv')
    _set_address('nas-b')
    rm.transfer(str(remote_env), '/media/Movies/a.mkv')
    assert [c.get('transfer_host') for c in calls] == ['nas-a', None, 'nas-b']


def test_fallback_log_rate_limited_per_hour_per_address(remote_env, monkeypatch, caplog, fresh_state):
    clock = fresh_state
    _set_address('nas-10g')
    _capture(monkeypatch, replies=[FAIL, OK] * 6)
    with caplog.at_level(logging.WARNING, logger='app.remote_mover'):
        for _ in range(4):                                           # every 15 min for 45 min
            rm.transfer(str(remote_env), '/media/Movies/a.mkv')
            clock.t += rm.TRANSFER_COOLDOWN_S
        assert len(_fallback_logs(caplog, 'nas-10g')) == 1
        clock.t += rm.TRANSFER_LOG_INTERVAL_S - 4 * rm.TRANSFER_COOLDOWN_S   # an hour after the first line
        rm.transfer(str(remote_env), '/media/Movies/a.mkv')
        assert len(_fallback_logs(caplog, 'nas-10g')) == 2
        _set_address('nas-other')
        clock.t += rm.TRANSFER_COOLDOWN_S
        rm.transfer(str(remote_env), '/media/Movies/a.mkv')
        assert len(_fallback_logs(caplog, 'nas-other')) == 1


# ------------------------------------------------------------------ Plex untouched

def test_plex_and_library_api_never_read_transfer_address():
    root = pathlib.Path(__file__).resolve().parents[1] / 'app'
    readers = sorted(p.name for p in root.rglob('*.py') if 'transfer_address' in p.read_text(errors='ignore'))
    assert readers == ['file_management.py', 'remote_mover.py']


def test_plex_url_unchanged_by_transfer_address(minimal, media, monkeypatch):  # noqa: F811
    from app.main import settings
    before = settings.get('plex.url')
    c = _client(minimal)
    r = c.put(MAPPING, json={'transfer_address': '10.10.10.25'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200, r.get_json()
    assert settings.get('plex.url') == before


# ------------------------------------------------------------------ save

def test_save_validates_format_only_and_persists(minimal, media, monkeypatch):  # noqa: F811
    from app import adb_utils
    monkeypatch.setattr(adb_utils, 'tcp_probe', lambda *a, **k: pytest.fail('save must not probe'))
    c = _client(minimal)
    r = c.put(MAPPING, json={'transfer_address': ' 10.10.10.25 '}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['transfer_address'] == '10.10.10.25'
    assert fm.load()['transfer_address'] == '10.10.10.25'
    r = c.put(MAPPING, json={'transfer_address': 'down.lan'}, environ_base=REMOTE, headers=HDR)   # unreachable is fine
    assert r.status_code == 200 and r.get_json()['transfer_address'] == 'down.lan'
    for bad in ('smb://x', '10.0.0.1:445', 'a b', 5):
        r = c.put(MAPPING, json={'transfer_address': bad}, environ_base=REMOTE, headers=HDR)
        assert r.status_code == 400 and r.get_json()['errors']['transfer_address'] == fm.INVALID_ADDRESS, bad
    assert fm.load()['transfer_address'] == 'down.lan'
    r = c.put(MAPPING, json={'transfer': 'hardlink'}, environ_base=REMOTE, headers=HDR)   # untouched by others
    assert r.get_json()['transfer_address'] == 'down.lan'
    r = c.put(MAPPING, json={'transfer_address': ''}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['transfer_address'] == ''


# ------------------------------------------------------------------ reference helper

def test_reference_helper_forwards_env_without_renames(monkeypatch):
    import importlib.util
    path = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'luma-remote-mover-host.py'
    spec = importlib.util.spec_from_file_location('mover_host_ta', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'SSH_TARGET', 'u@dl')
    plain = mod.build_cmd('cp', '/scratch/downloads/a b.mkv', '/media/M/a b.mkv')
    assert 'LUMA_TRANSFER_HOST' not in plain[-1]
    withh = mod.build_cmd('cp', '/scratch/downloads/a b.mkv', '/media/M/a b.mkv', '', '10.10.10.25')
    assert withh[-1] == 'LUMA_TRANSFER_HOST=10.10.10.25 ' + plain[-1]
    assert mod.run_move('cp', '/scratch/downloads/a', '/media/a', '', 'x;rm -rf /')[0] == 2


# ------------------------------------------------------------------ B1: option injection (helper + CLI)

BAD_HOSTS = ['-Jx', '-oProxyCommand', 'a b', 'a;b', 'u@h', '[::1]', '-Jevil.com', 'x$(id)', '127.0.0.1', '::1',
             '169.254.169.254', '224.0.0.1', '0.0.0.0', 'fe80::1', 'ff02::1', '']
GOOD_HOSTS = ['10.10.10.25', '192.168.1.5', '172.16.0.9', 'nas', 'nas.lan', 'ffserver.lan', 'fd00::25', '2001:db8::5']


def _helper():
    import importlib.util
    path = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'luma-remote-mover-host.py'
    spec = importlib.util.spec_from_file_location('mover_host_b1', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize('host', BAD_HOSTS)
def test_helper_rejects_bad_transfer_host(host, monkeypatch):
    mod = _helper()
    assert not mod.valid_transfer_host(host)
    assert not fm.valid_address(host)
    monkeypatch.setattr(mod.subprocess, 'run', lambda *a, **k: pytest.fail('must not run'))
    if host:
        rc, out = mod.run_move('cp', '/scratch/downloads/a', '/media/a', '', host)
        assert rc == 2 and 'transfer_host' in out
    monkeypatch.setattr(mod, 'SSH_TARGET', 'u@dl')
    assert 'LUMA_TRANSFER_HOST' not in mod.build_cmd('cp', '/scratch/downloads/a', '/media/a', '', host)[-1]


@pytest.mark.parametrize('host', GOOD_HOSTS)
def test_helper_and_app_accept_lan_hosts(host):
    assert _helper().valid_transfer_host(host)
    assert fm.valid_address(host)


def _run_cli(tmp_path, transfer_host):
    import os
    import subprocess
    script = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'luma-host-remote-mover.sh'
    bindir = tmp_path / 'bin'
    bindir.mkdir(exist_ok=True)
    fake = bindir / 'ssh'
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "${@: -1}"\n')
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f'{bindir}:{os.environ["PATH"]}', LUMA_REMOTE_SSH_HOST='dl', LUMA_TRANSFER_HOST=transfer_host)
    return subprocess.run(['bash', str(script), 'cp', '/scratch/downloads/a b', '/media/M/a b'],
                          capture_output=True, text=True, env=env, timeout=20)


@pytest.mark.parametrize('host', [h for h in BAD_HOSTS if h])
def test_cli_ignores_bad_transfer_host_with_a_log_line(tmp_path, host):
    p = _run_cli(tmp_path, host)
    assert p.returncode == 0, p.stderr
    assert 'LUMA_TRANSFER_HOST' not in p.stdout
    assert 'invalid LUMA_TRANSFER_HOST ignored; using the main address' in p.stderr


@pytest.mark.parametrize('host', GOOD_HOSTS)
def test_cli_forwards_good_transfer_host(tmp_path, host):
    p = _run_cli(tmp_path, host)
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith(f'LUMA_TRANSFER_HOST={host} ') and p.stderr == ''


def test_cli_blank_is_unchanged(tmp_path):
    p = _run_cli(tmp_path, '')
    assert p.returncode == 0 and 'LUMA_TRANSFER_HOST' not in p.stdout and p.stderr == ''


# ------------------------------------------------------------------ PM: .72 mover exits 2 -> fallback

def test_mover_exit_2_relayed_by_helper_falls_back_to_main(remote_env, monkeypatch, caplog):
    """The .72 mover exits 2 when it can't mount the share via the transfer host; the helper relays
    HTTP 500 {"ok": false, "rc": 2}: an outright failure, so the copy runs once on the main address."""
    _set_address('10.10.10.25')
    calls = _capture(monkeypatch, replies=[
        (500, json.dumps({'ok': False, 'rc': 2, 'output': 'mount via transfer host failed'})), OK])
    with caplog.at_level(logging.WARNING, logger='app.remote_mover'):
        assert rm.transfer(str(remote_env), '/media/Movies/a.mkv') == 'copied'
    assert [c.get('transfer_host') for c in calls] == ['10.10.10.25', None]
    assert len(_fallback_logs(caplog, '10.10.10.25')) == 1


def test_mover_exit_2_with_observable_dest_still_falls_back(remote_env, monkeypatch):
    _set_address('10.10.10.25')
    monkeypatch.setattr(rm, '_dest_observable', lambda d: True)
    monkeypatch.setattr(rm, '_verify_final', lambda *a: None)
    calls = _capture(monkeypatch, replies=[(500, json.dumps({'ok': False, 'rc': 2, 'output': 'x'})), OK])
    assert rm.transfer(str(remote_env), '/media/Movies/a.mkv') == 'copied'
    assert [c.get('transfer_host') for c in calls] == ['10.10.10.25', None]


def test_unusual_500_without_script_rc_is_transient_not_fallback(remote_env, monkeypatch):
    _set_address('10.10.10.25')
    monkeypatch.setattr(rm, '_dest_observable', lambda d: False)
    calls = _capture(monkeypatch, replies=[(500, json.dumps({'ok': False, 'rc': 1, 'output': 'boom'}))])
    with pytest.raises(rm.RemoteTransient):
        rm.transfer(str(remote_env), '/media/Movies/a.mkv')
    assert len(calls) == 1 and rm._transfer_cooldown_until == {}
