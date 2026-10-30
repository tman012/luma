"""Taylor 2026-10-29: Genre Roulette unlocked on test (follows LUMA_TEST_ALLOW); Franchise Completer and 4K
auto-grab stay off on test (v198). qBittorrent add replies (text or 5.2.0+ JSON) read the same everywhere."""
import pytest

from app import instance
from app.qbit_connector import add_ok

LOCKED = ('franchise_completer', 'upgrade_4k')


@pytest.fixture
def test_instance(monkeypatch):
    monkeypatch.setenv('LUMA_INSTANCE', 'test')
    monkeypatch.delenv('LUMA_TEST_ALLOW', raising=False)
    yield
    instance.uninstall_write_guard()


class FakeSettings:
    def __init__(self, d):
        self.d = d

    def get(self, k, default=None):
        return self.d.get(k, default)


def test_roulette_allowed_on_test_when_opted_in(test_instance, monkeypatch):
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'roulette')
    assert instance.writes_allowed('roulette')
    instance.require('roulette')


def test_roulette_off_on_test_when_not_allowed(test_instance, monkeypatch):
    assert not instance.writes_allowed('roulette')
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'movie_night')
    assert not instance.writes_allowed('roulette')
    monkeypatch.setenv('LUMA_INSTANCE', 'Test')                 # opt-ins honored only for exactly 'test'
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'roulette')
    assert not instance.writes_allowed('roulette')


@pytest.mark.parametrize('job', LOCKED)
def test_fc_and_4k_stay_off_on_test_even_when_listed(test_instance, monkeypatch, job):
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'roulette,franchise_completer,upgrade_4k,arr,qbit')
    assert not instance.writes_allowed(job)
    with pytest.raises(instance.WritesBlocked):
        instance.require(job)
    monkeypatch.setattr(instance, 'is_test', lambda: False)                 # prod keeps them
    assert instance.writes_allowed(job)


def test_roulette_uses_saved_hour(monkeypatch):
    from app import genre_roulette as gr
    s = FakeSettings({'automation.genre_roulette.enabled': True, 'automation.genre_roulette.hour': 22,
                      'automation.genre_roulette.minute': 50})
    assert gr._scheduled_time(s) == '22:50'


class R:
    def __init__(self, code, text):
        self.status_code, self.text, self.content = code, text, text.encode()


@pytest.mark.parametrize('code,body,ok', [
    (200, 'Ok.', True), (200, '', True), (204, '', True),
    (200, '{"added_torrent_ids":["f85a"],"failure_count":0}', True),
    (200, '{"failure_count":0}', True),
    (200, '{"added_torrent_ids":[],"failure_count":1}', False),
    (200, '{"added_torrent_ids":["a"],"failure_count":2}', False),
    (200, '{"added_torrent_ids":["a"]}', False),
    (200, '{"failure_count":"0"}', False),
    (200, '{broken', False), (200, 'Fails.', False), (200, '[]', False),
    (409, 'Ok.', False), (500, '', False), (415, 'Fails.', False),
])
def test_add_ok(code, body, ok):
    assert add_ok(R(code, body)) is ok


def test_every_add_site_uses_add_ok():
    import os
    import app
    root = os.path.dirname(app.__file__)
    for f in ('tv_grab.py', 'movie_scanner.py'):
        src = open(os.path.join(root, f)).read()
        assert 'add_ok(r)' in src, f
        assert "not in ('Ok.', '')" not in src, f



def test_minimal_start_services_does_not_start_fc_or_4k():
    import os
    import app
    src = open(os.path.join(os.path.dirname(app.__file__), 'main.py')).read()
    head, _, _ = src.partition("    # Initialize library caches\n")
    minimal = head[head.index("    if _minimal.enabled():\n        logger.warning('LUMA_MINIMAL is set"):]
    assert 'start_franchise_completer_thread' not in minimal and 'start_upgrade_4k_auto_grab_thread' not in minimal


@pytest.mark.parametrize('mod,fn,job', [('franchise_completer', 'start_franchise_completer_thread', 'franchise_completer'),
                                        ('upgrade_4k_auto_grab', 'start_upgrade_4k_auto_grab_thread', 'upgrade_4k')])
def test_fc_and_4k_threads_never_start_on_test(test_instance, monkeypatch, mod, fn, job):
    import importlib
    import threading
    m = importlib.import_module(f'app.{mod}')
    started = []
    monkeypatch.setattr(threading.Thread, 'start', lambda self: started.append(self.name))
    monkeypatch.setenv('LUMA_TEST_ALLOW', job)
    getattr(m, fn)()
    assert started == []


@pytest.mark.parametrize('inst,expect', [('test', []), ('prod', ['gr'])])
def test_legacy_radarr_roulette_thread_never_starts_on_test(monkeypatch, inst, expect):
    """No Radarr/Sonarr on test: the legacy GR thread stays off on test even with roulette allowed and
    LUMA_MINIMAL off; prod still starts it."""
    from app import genre_roulette, main
    monkeypatch.setenv('LUMA_INSTANCE', inst)
    monkeypatch.setenv('LUMA_TEST_ALLOW', 'roulette')
    monkeypatch.delenv('LUMA_MINIMAL', raising=False)
    monkeypatch.setattr(instance, 'test_signs', lambda: [], raising=False)   # prod here means a real prod host
    started = []
    monkeypatch.setattr(genre_roulette, 'start_genre_roulette_thread', lambda: started.append('gr'))
    try:
        assert main._start_legacy_genre_roulette() == bool(expect)
    finally:
        instance.uninstall_write_guard()
    assert started == expect
