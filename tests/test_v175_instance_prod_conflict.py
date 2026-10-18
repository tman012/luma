"""v175 (PM ruling on a Tester BLOCKER): LUMA_INSTANCE=prod on a test-like host (any LUMA_TEST_* variable,
port 5002, a luma-test data folder, or the marker file in the data folder) fails closed: every gated job
OFF (LUMA_TEST_ALLOW is no override) and one ERROR at boot naming the signs. LUMA_INSTANCE set but empty
is unknown: fails closed with an ERROR. Unset keeps today's detection. Today's prod shape (port 5001,
~/media-search-app/data, no marker, no LUMA_TEST_*, LUMA_INSTANCE unset) and the same with
LUMA_INSTANCE=prod keep every job ON."""
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from app import instance

SIGN_ENVS = ('LUMA_TEST_QBIT_TAG', 'LUMA_TEST_TV_TAG', 'LUMA_TEST_ALLOW', 'LUMA_PORT', 'PORT', 'LUMA_BIND', 'LUMA_INSTANCE')
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def prod_shape(monkeypatch, tmp_path):
    """Today's prod: port 5001, data in ~/media-search-app/data, no marker, no LUMA_TEST_*, unset."""
    for k in SIGN_ENVS:
        monkeypatch.delenv(k, raising=False)
    d = tmp_path / 'home' / 'media-search-app' / 'data'
    d.mkdir(parents=True)
    monkeypatch.setenv('LUMA_DATA_DIR', str(d))
    monkeypatch.setenv('PORT', '5001')
    monkeypatch.setattr(instance, '_logged', [False])
    yield d
    instance.uninstall_write_guard()


def _sign(name, monkeypatch, tmp_path, data):
    if name in ('LUMA_TEST_QBIT_TAG', 'LUMA_TEST_TV_TAG'):
        monkeypatch.setenv(name, 'luma-test')
    elif name == 'LUMA_TEST_ALLOW':
        monkeypatch.setenv(name, 'tv_grab,qbit,arr,downloads_guard')     # an opt-in is no override either
    elif name in ('PORT', 'LUMA_PORT'):
        monkeypatch.setenv(name, '5002')
    elif name == 'LUMA_BIND':
        monkeypatch.setenv(name, '0.0.0.0:5002')
    elif name == 'data_dir':
        d = tmp_path / 'luma-test' / 'data'
        d.mkdir(parents=True)
        monkeypatch.setenv('LUMA_DATA_DIR', str(d))
    elif name == 'marker':
        (data / instance.MARKER).write_text('')
    else:
        raise AssertionError(name)


SIGNS = ['LUMA_TEST_QBIT_TAG', 'LUMA_TEST_TV_TAG', 'LUMA_TEST_ALLOW', 'PORT', 'LUMA_PORT', 'LUMA_BIND', 'data_dir', 'marker']


def _all_off():
    return set(instance.blocked_jobs()) == set(instance.GATED) and not any(instance.writes_allowed(j) for j in instance.GATED)


def _errors(caplog):
    return [r for r in caplog.records if r.name == 'app.instance' and r.levelno >= logging.ERROR]


@pytest.mark.parametrize('value', [None, 'prod'])
def test_todays_prod_shape_keeps_every_job_on(monkeypatch, caplog, value):
    if value:
        monkeypatch.setenv('LUMA_INSTANCE', value)
    assert instance.test_signs() == [] and instance.name() == 'prod' and not instance.is_test()
    assert all(instance.writes_allowed(j) for j in instance.GATED) and instance.blocked_jobs() == {}
    assert instance.install_write_guard() is False
    with caplog.at_level(logging.INFO, logger='app.instance'):
        instance.log_blocked_once()
    assert [r for r in caplog.records if r.name == 'app.instance'] == []


def test_repro_prod_on_port_5002_is_all_off_with_the_error(monkeypatch, caplog):
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')
    monkeypatch.setenv('PORT', '5002')
    assert instance.is_test() and _all_off() and instance.install_write_guard() is True
    with caplog.at_level(logging.INFO, logger='app.instance'):
        instance.log_blocked_once()
        instance.log_blocked_once()
    errs = _errors(caplog)
    assert len(errs) == 1 and 'LUMA_INSTANCE=prod' in errs[0].getMessage() and 'port 5002' in errs[0].getMessage()


@pytest.mark.parametrize('sign', SIGNS)
def test_prod_with_each_test_sign_fails_closed(monkeypatch, tmp_path, prod_shape, caplog, sign):
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')
    _sign(sign, monkeypatch, tmp_path, prod_shape)
    signs = instance.test_signs()
    assert signs and instance.prod_conflict() == signs
    assert instance.is_test() and _all_off()
    with caplog.at_level(logging.INFO, logger='app.instance'):
        instance.log_blocked_once()
    errs = _errors(caplog)
    assert len(errs) == 1 and ', '.join(signs) in errs[0].getMessage() and 'no override' in errs[0].getMessage()
    off = [r.getMessage() for r in caplog.records if r.getMessage().startswith('Instance test: ')]
    assert len(off) == len(instance.GATED) and not any('opt in' in m for m in off)


@pytest.mark.parametrize('sign', SIGNS)
def test_unset_with_each_sign_keeps_todays_detection(monkeypatch, tmp_path, prod_shape, sign):
    _sign(sign, monkeypatch, tmp_path, prod_shape)
    assert instance.is_test() and instance.assumed_test() and not instance.prod_conflict()


@pytest.mark.parametrize('sign', [None] + SIGNS)
def test_empty_is_unknown_and_fails_closed_with_an_error(monkeypatch, tmp_path, prod_shape, caplog, sign):
    monkeypatch.setenv('LUMA_INSTANCE', '')
    if sign:
        _sign(sign, monkeypatch, tmp_path, prod_shape)
    assert instance.is_test() and instance.unrecognized() and not instance.assumed_test()
    if sign != 'LUMA_TEST_ALLOW':
        assert _all_off()
    with caplog.at_level(logging.INFO, logger='app.instance'):
        instance.log_blocked_once()
    errs = _errors(caplog)
    assert len(errs) == 1 and "LUMA_INSTANCE=''" in errs[0].getMessage()


def _boot(tmp_path, data, env_extra):
    (tmp_path / 'logs').mkdir(exist_ok=True)
    env = {'HOME': str(tmp_path / 'home'), 'PATH': '/usr/bin:/bin', 'LUMA_MINIMAL': '1',
           'DATABASE_PATH': str(data / 'luma.db'), 'LUMA_DATA_DIR': str(data),
           'LUMA_CACHE_DIR': str(tmp_path / 'cache'), 'LUMA_LOG_DIR': str(tmp_path / 'logs'),
           'LUMA_SKIP_BACKGROUND_THREADS': '1', 'FLASK_STATIC_FOLDER': str(REPO / 'static'), **env_extra}
    code = ('import app.main, json; from app import instance; '
            'print("BLOCKED=" + json.dumps(sorted(instance.blocked_jobs())))')
    out = subprocess.run([sys.executable, '-c', code], cwd=str(REPO), env=env, capture_output=True, text=True, timeout=240)
    assert out.returncode == 0, out.stderr[-2000:]
    blocked = next(ln for ln in out.stdout.splitlines() if ln.startswith('BLOCKED='))
    return out.stdout + out.stderr, json.loads(blocked.split('=', 1)[1])


@pytest.mark.parametrize('extra', [{'PORT': '5001'}, {'PORT': '5001', 'LUMA_INSTANCE': 'prod'}])
def test_a_real_boot_of_todays_prod_shape_has_every_job_on(tmp_path, prod_shape, extra):
    text, blocked = _boot(tmp_path, prod_shape, extra)
    assert blocked == []
    assert 'looks like the TEST install' not in text and '!!!' not in text


def test_a_real_boot_of_prod_on_5002_is_all_off_with_one_error(tmp_path, prod_shape):
    text, blocked = _boot(tmp_path, prod_shape, {'PORT': '5002', 'LUMA_INSTANCE': 'prod'})
    assert set(blocked) == set(instance.GATED)
    lines = [ln for ln in text.splitlines() if 'LUMA_INSTANCE=prod but this host looks like the TEST install' in ln]
    assert len(lines) == 1 and ' - ERROR - ' in lines[0] and 'port 5002' in lines[0]
