"""v177 (Tester BLOCKER B1): port 5002 is a test sign by value, the way the server parses it
(server_config.get_port() int()s LUMA_PORT; PORT; LUMA_BIND's port), not by spelling. Prod on such a
host: all 21 gated jobs OFF and one ERROR. FF-17: every "opt-ins ignored" line is an ERROR naming
LUMA_TEST_ALLOW. Ops: each honored opt-in is stated at boot ("Instance test: tv_grab is ON (test, opt-in)")."""
import logging

import pytest

from app import instance
from tests.test_v175_instance_prod_conflict import ALL_JOBS, prod_shape  # noqa: F401

SPELLINGS = ['5002', '05002', '+5002', '5_002', ' 5002 ', '\uff15\uff10\uff10\uff12', '\u0665\u0660\u0660\u0662', '0005002']
BINDS = ['0.0.0.0:05002', 'host:05002', '[::]:+5002', '127.0.0.1:\uff15\uff10\uff10\uff12', '0.0.0.0:\u0665\u0660\u0660\u0662',
         '[::1]:5_002', '05002']


def _prod_all_off(caplog):
    with caplog.at_level(logging.INFO, logger='app.instance'):
        instance.log_blocked_once()
    assert instance.is_test() and len(instance.blocked_jobs()) == 21 == len(instance.GATED)
    errs = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errs) == 1 and 'port 5002' in errs[0].getMessage() and 'LUMA_TEST_ALLOW opt-ins are ignored' in errs[0].getMessage()


@pytest.mark.parametrize('var', ['LUMA_PORT', 'PORT'])
@pytest.mark.parametrize('raw', SPELLINGS)
def test_prod_on_a_5002_port_in_any_spelling_is_all_off(monkeypatch, caplog, var, raw):
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')
    monkeypatch.setenv(var, raw)
    if var == 'LUMA_PORT':
        from app import server_config
        assert server_config.get_port() == 5002                # what the server would bind
    _prod_all_off(caplog)


@pytest.mark.parametrize('bind', BINDS)
def test_prod_on_a_5002_bind_in_any_spelling_is_all_off(monkeypatch, caplog, bind):
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')
    monkeypatch.setenv('LUMA_BIND', bind)
    _prod_all_off(caplog)


@pytest.mark.parametrize('raw', ['5001', '50020', '500', '5002.0', '0x138a', 'abc', '', '[::1]', '0.0.0.0:5001'])
def test_other_ports_are_no_sign(monkeypatch, raw):
    monkeypatch.setenv('LUMA_INSTANCE', 'prod')
    monkeypatch.setenv('PORT', raw)
    monkeypatch.setenv('LUMA_BIND', raw)
    assert not instance.is_test() and instance.blocked_jobs() == {}


def test_unset_with_opt_ins_on_a_test_like_host_is_an_error(monkeypatch, caplog):
    monkeypatch.setenv('LUMA_PORT', '05002')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab')
    with caplog.at_level(logging.INFO, logger='app.instance'):
        instance.log_blocked_once()
    errs = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errs) == 1 and 'LUMA_TEST_ALLOW opt-ins are ignored' in errs[0]
    assert not any('is ON (test, opt-in)' in r.getMessage() for r in caplog.records)


def test_each_honored_opt_in_is_stated_at_boot(monkeypatch, caplog):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'tv_grab,movie_grab,nonsense')
    with caplog.at_level(logging.INFO, logger='app.instance'):
        instance.log_blocked_once()
    on = sorted(r.getMessage() for r in caplog.records if 'is ON (test, opt-in)' in r.getMessage())
    assert on == ['Instance test: movie_grab is ON (test, opt-in)', 'Instance test: tv_grab is ON (test, opt-in)']
    assert all(r.levelno == logging.INFO for r in caplog.records if 'is ON' in r.getMessage())
    assert len(instance.blocked_jobs()) == 19
