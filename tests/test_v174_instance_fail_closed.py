"""v174 (PM's rule for Tester FF-10, HIGH): LUMA_INSTANCE exactly 'prod' or 'test' behaves as before.
ANY other non-empty value (typos, case variants, whitespace, 'Test-2') fails closed: all 21 gated jobs
OFF, the write guard on, and an ERROR line at boot naming the value. Unset keeps the v158c
detection (v175: empty is unknown and fails closed). Production's compose sets no LUMA_INSTANCE: unchanged."""
import logging

import pytest

from app import instance

SIGN_ENVS = ('LUMA_TEST_QBIT_TAG', 'LUMA_TEST_TV_TAG', 'LUMA_TEST_ALLOW', 'LUMA_PORT', 'PORT', 'LUMA_BIND')


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for k in SIGN_ENVS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('LUMA_DATA_DIR', str(tmp_path / 'data'))
    (tmp_path / 'data').mkdir()
    monkeypatch.setattr(instance, '_logged', [False])
    yield
    instance.uninstall_write_guard()


TYPOS = ['Test-2', 'bogus', 'prd', 'pord', 'prod1', 'tset', 'tes', 'testt', 'production', 'live', 'dev', 'staging',
         '0', 'false', 'none', 'p r o d', 'tést', '   ']
CASE_SPACE = ['PROD', 'Prod', 'pRoD', 'TEST', 'Test', 'tEST', ' prod', 'prod ', ' test', 'test ', '\ttest\n', 'prod\n']


@pytest.mark.parametrize('value', TYPOS + CASE_SPACE)
@pytest.mark.parametrize('port', [None, '5001', '5002'])
def test_an_unrecognized_value_is_test_and_every_gated_job_is_off(monkeypatch, value, port):
    monkeypatch.setenv('LUMA_INSTANCE', value)
    if port:
        monkeypatch.setenv('PORT', port)
    assert instance.is_test() and instance.name() == 'test'
    assert instance.unrecognized()
    assert set(instance.blocked_jobs()) == set(instance.GATED) and len(instance.GATED) == 20  # v254: daily_missing is DATA_DIR_ONLY
    assert not any(instance.writes_allowed(j) for j in instance.GATED)
    assert instance.install_write_guard() is True


def test_exactly_test_is_test_without_the_error(monkeypatch, caplog):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    assert instance.is_test() and not instance.unrecognized()
    with caplog.at_level(logging.INFO, logger='app.instance'):
        instance.log_blocked_once()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len([r for r in caplog.records if ' is OFF ' in r.getMessage()]) == 20


@pytest.mark.parametrize('value', [None, 'prod'])        # v175: '' is unknown now (fails closed)
def test_prod_is_unchanged(monkeypatch, value):
    if value is None:
        monkeypatch.delenv('LUMA_INSTANCE', raising=False)
    else:
        monkeypatch.setenv('LUMA_INSTANCE', value)
    assert not instance.is_test() and not instance.unrecognized()
    assert all(instance.writes_allowed(j) for j in instance.GATED)
    assert instance.blocked_jobs() == {} and instance.install_write_guard() is False


@pytest.mark.parametrize('value', [None])                # v175: '' is unknown (test_v175_instance_prod_conflict)
def test_unset_with_a_test_sign_is_still_test(monkeypatch, value):
    if value is None:
        monkeypatch.delenv('LUMA_INSTANCE', raising=False)
    else:
        monkeypatch.setenv('LUMA_INSTANCE', value)
    monkeypatch.setenv('PORT', '5002')
    assert instance.is_test() and instance.assumed_test() and not instance.unrecognized()


def test_exact_prod_no_longer_beats_test_signs(monkeypatch):
    # v175 (PM, Tester BLOCKER): prod on a test-like host fails closed (test_v175_instance_prod_conflict)
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')
    monkeypatch.setenv('PORT', '5002')
    assert instance.is_test() and instance.prod_conflict() == ['port 5002']


@pytest.mark.parametrize('value', TYPOS + CASE_SPACE)
def test_an_unrecognized_value_logs_an_error_naming_it(monkeypatch, value, caplog):
    monkeypatch.setenv('LUMA_INSTANCE', value)
    with caplog.at_level(logging.INFO, logger='app.instance'):
        instance.log_blocked_once()
    warn = [r for r in caplog.records if r.levelno == logging.ERROR and 'not recognized' in r.getMessage()]
    assert len(warn) == 1 and repr(value) in warn[0].getMessage() and 'treating it as test' in warn[0].getMessage()
    offs = [r for r in caplog.records if r.getMessage().startswith('Instance test: ') and ' is OFF ' in r.getMessage()]
    assert len(offs) == 20
