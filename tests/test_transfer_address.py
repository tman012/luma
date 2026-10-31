"""Transfer address: optional second library-server address for the download-host -> library
copy (remote push-import / mover). Blank keeps the old path, set sends it, unreachable or a
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

def test_blank_keeps_old_body(remote_env, monkeypatch):
    probed = []
    monkeypatch.setattr(fm, 'address_reachable', lambda a, *k: probed.append(a) or True)
    calls = _capture(monkeypatch)
    assert rm.transfer(str(remote_env), '/media/Movies/a.mkv') == 'copied'
    assert len(calls) == 1
    body = calls[0]
    assert re.fullmatch(r'/media/Movies/\.luma-[0-9a-f]{16}\.luma-partial', body.pop('tmp'))
    assert body == {'mode': 'cp', 'src': '/scratch/downloads/a.mkv', 'dest': '/media/Movies/a.mkv'}
    assert probed == []   # blank: nothing probed


def test_set_uses_transfer_address(remote_env, monkeypatch):
    _set_address('10.10.10.25')
    monkeypatch.setattr(fm, 'address_reachable', lambda a, *k: True)
    calls = _capture(monkeypatch)
    assert rm.transfer(str(remote_env), '/media/Movies/a.mkv') == 'copied'
    assert len(calls) == 1 and calls[0]['transfer_host'] == '10.10.10.25'
    assert calls[0]['src'] == '/scratch/downloads/a.mkv' and calls[0]['dest'] == '/media/Movies/a.mkv'


def test_unreachable_falls_back_and_logs(remote_env, monkeypatch, caplog):
    _set_address('10.10.10.25')
    monkeypatch.setattr(fm, 'address_reachable', lambda a, *k: False)
    calls = _capture(monkeypatch)
    with caplog.at_level(logging.WARNING, logger='app.remote_mover'):
        assert rm.transfer(str(remote_env), '/media/Movies/a.mkv') == 'copied'
    assert len(calls) == 1 and 'transfer_host' not in calls[0]
    assert any('10.10.10.25 unreachable; using the main address' in r.getMessage() for r in caplog.records)


def test_failed_copy_via_transfer_retries_on_main_and_logs(remote_env, monkeypatch, caplog):
    _set_address('nas-10g')
    monkeypatch.setattr(fm, 'address_reachable', lambda a, *k: True)
    calls = _capture(monkeypatch, replies=[
        (500, json.dumps({'ok': False, 'rc': 5, 'output': 'copy failed'})),
        (200, json.dumps({'ok': True, 'rc': 0, 'output': ''})),
    ])
    with caplog.at_level(logging.WARNING, logger='app.remote_mover'):
        assert rm.transfer(str(remote_env), '/media/Movies/a.mkv') == 'copied'
    assert [c.get('transfer_host') for c in calls] == ['nas-10g', None]
    assert calls[0]['tmp'] == calls[1]['tmp']
    assert any('via transfer address nas-10g failed' in r.getMessage() for r in caplog.records)


def test_transient_failure_is_not_retried_on_main(remote_env, monkeypatch):
    _set_address('nas-10g')
    monkeypatch.setattr(fm, 'address_reachable', lambda a, *k: True)
    monkeypatch.setattr(rm, '_dest_observable', lambda d: False)
    calls = _capture(monkeypatch, replies=[(500, json.dumps({'ok': False, 'rc': 255, 'output': 'ssh: connection refused'}))])
    with pytest.raises(rm.RemoteTransient):
        rm.transfer(str(remote_env), '/media/Movies/a.mkv')
    assert len(calls) == 1


# ------------------------------------------------------------------ Plex untouched

def test_plex_and_library_api_never_read_transfer_address():
    root = pathlib.Path(__file__).resolve().parents[1] / 'app'
    readers = sorted(p.name for p in root.rglob('*.py') if 'transfer_address' in p.read_text(errors='ignore'))
    assert readers == ['file_management.py', 'remote_mover.py']


def test_plex_url_unchanged_by_transfer_address(minimal, media, monkeypatch):  # noqa: F811
    from app.main import settings
    before = settings.get('plex.url')
    monkeypatch.setattr(fm, 'address_reachable', lambda a, *k: True)
    c = _client(minimal)
    r = c.put(MAPPING, json={'transfer_address': '10.10.10.25'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200, r.get_json()
    assert settings.get('plex.url') == before


# ------------------------------------------------------------------ save

def test_save_validates_and_persists(minimal, media, monkeypatch):  # noqa: F811
    reach = {'10.10.10.25': True, 'down.lan': False}
    probed = []
    monkeypatch.setattr(fm, 'address_reachable', lambda a, *k: probed.append(a) or reach.get(a, False))
    c = _client(minimal)
    r = c.put(MAPPING, json={'transfer_address': ' 10.10.10.25 '}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['transfer_address'] == '10.10.10.25'
    assert fm.load()['transfer_address'] == '10.10.10.25'
    r = c.put(MAPPING, json={'transfer_address': 'down.lan'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and r.get_json()['errors']['transfer_address'] == fm.UNREACHABLE_ADDRESS
    r = c.put(MAPPING, json={'transfer_address': 'smb://x'}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 400 and r.get_json()['errors']['transfer_address'] == fm.INVALID_ADDRESS
    assert fm.load()['transfer_address'] == '10.10.10.25'
    r = c.put(MAPPING, json={'transfer': 'hardlink'}, environ_base=REMOTE, headers=HDR)   # untouched by others
    assert r.get_json()['transfer_address'] == '10.10.10.25'
    r = c.put(MAPPING, json={'transfer_address': ''}, environ_base=REMOTE, headers=HDR)
    assert r.status_code == 200 and r.get_json()['transfer_address'] == ''
    assert probed == ['10.10.10.25', 'down.lan']


def test_reachable_uses_tcp_probe(monkeypatch):
    from app import adb_utils
    seen = []
    monkeypatch.setattr(adb_utils, 'tcp_probe', lambda h, p, t: seen.append((h, p)) or {'ok': p == 445})
    assert fm.address_reachable('10.0.0.9') is True
    assert seen == [('10.0.0.9', 22), ('10.0.0.9', 445)]
    monkeypatch.setenv('LUMA_TRANSFER_PROBE_PORTS', '873')
    assert fm.address_reachable('10.0.0.9') is False


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
