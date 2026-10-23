"""v251: /api/home answers with every ready rail in one response; a rail that errors or misses the
HOME_DEADLINE_S budget is dropped (never held for, never a skeleton)."""

import threading
import time
import xml.etree.ElementTree as ET

import pytest

from tests.test_home_rails import REMOTE, FakePlex, _client, plex  # noqa: F401  (fixture)


def test_deadline_is_under_the_phone_timeout():
    from app import home_rails
    assert home_rails.HOME_DEADLINE_S == 6.0
    # frontend MinimalHomePage SKELETON_TIMEOUT_MS = 7000: server budget + 1 s to reach the phone
    src = open('frontend/src/pages/MinimalHomePage.tsx').read()
    assert 'export const SKELETON_TIMEOUT_MS = 7000;' in src


def test_all_ready_one_response(test_app, plex):  # noqa: F811
    body = _client(test_app).get('/api/home', environ_base=REMOTE).json
    assert body['plex'] is True
    assert [r['title'] for r in body['rails']] == ['Movies', 'TV Shows', 'Kids']
    assert len(body['continue_watching']) == 2


def test_a_failed_rail_is_dropped_and_the_rest_render(test_app, plex, monkeypatch):  # noqa: F811
    real = FakePlex._get

    def get(self, path, params=None, timeout=15):
        if path.startswith('/library/sections/2/'):
            raise RuntimeError('plex 500')
        return real(self, path, params, timeout)
    monkeypatch.setattr(FakePlex, '_get', get)
    r = _client(test_app).get('/api/home', environ_base=REMOTE)
    assert r.status_code == 200
    body = r.json
    assert body['plex'] is True
    assert [x['title'] for x in body['rails']] == ['Movies', 'Kids']
    assert len(body['continue_watching']) == 2


def test_a_failed_continue_watching_is_dropped(test_app, plex, monkeypatch):  # noqa: F811
    real = FakePlex._get

    def get(self, path, params=None, timeout=15):
        if path == '/library/onDeck':
            raise RuntimeError('plex 500')
        return real(self, path, params, timeout)
    monkeypatch.setattr(FakePlex, '_get', get)
    body = _client(test_app).get('/api/home', environ_base=REMOTE).json
    assert body['continue_watching'] == [] and len(body['rails']) == 3


def test_a_slow_rail_is_dropped_at_the_deadline(test_app, plex, monkeypatch):  # noqa: F811
    from app import home_rails
    monkeypatch.setattr(home_rails, 'HOME_DEADLINE_S', 0.4)
    release = threading.Event()
    real = FakePlex._get

    def get(self, path, params=None, timeout=15):
        if path.startswith('/library/sections/4/'):
            release.wait(5)
        return real(self, path, params, timeout)
    monkeypatch.setattr(FakePlex, '_get', get)
    t0 = time.monotonic()
    body = _client(test_app).get('/api/home', environ_base=REMOTE).json
    took = time.monotonic() - t0
    release.set()
    assert took < 2.0                                             # answered at the budget, not when Kids came back
    assert [x['title'] for x in body['rails']] == ['Movies', 'TV Shows']
    assert body['plex'] is True and len(body['continue_watching']) == 2


def test_every_rail_failing_reads_as_plex_down(test_app, plex, monkeypatch):  # noqa: F811
    real = FakePlex._get

    def get(self, path, params=None, timeout=15):
        if path == '/library/sections':
            return real(self, path, params, timeout)
        raise RuntimeError('plex 500')
    monkeypatch.setattr(FakePlex, '_get', get)
    body = _client(test_app).get('/api/home', environ_base=REMOTE).json
    assert body == {'continue_watching': [], 'rails': [], 'plex': False}
