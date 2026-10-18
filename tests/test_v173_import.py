"""v173 (Tester, ab030b7/607f36a FF-A and FF-B): Import from Prowlarr never answers 500 and never
holds a worker past its total deadline.

A fake Prowlarr session for the shapes; a loopback HTTP server (this test's own) for the slow
answers, because the deadline is about real sockets.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import tracker_import
from tests.test_tracker_import_v170 import _post, env, owner  # noqa: F401

GOOD = {'id': 50, 'name': 'Hostile', 'implementation': 'Torznab', 'enable': True,
        'capabilities': {'categories': [{'id': 2000}, {'id': 5000}]},
        'fields': [{'name': 'baseUrl', 'value': 'https://h.qa.example'}, {'name': 'apiKey', 'value': 'qa-h-key-0000'}]}
# Tester's hostile shapes (fastgate ab030b7 x/mock_prowlarr.py), each merged into one valid Torznab
SHAPES = {
    'fields_int': {'fields': 5}, 'fields_str': {'fields': 'abc'}, 'fields_badname': {'fields': [{'name': 5, 'value': 1}, 'x', None]},
    'caps_list': {'capabilities': [1, 2]}, 'caps_cats_int': {'capabilities': {'categories': 5}},
    'cats_str': {'capabilities': {'categories': [{'id': '5000'}, 'x', None]}},
    'urls_int': {'fields': [], 'indexerUrls': 5}, 'urls_dict': {'fields': [], 'indexerUrls': {'a': 1}},
    'urls_mixed': {'fields': [], 'indexerUrls': [5, None, 'https://u.qa.example']},
    'name_dict': {'name': {'x': 1}}, 'name_list': {'name': [1, 2]},
    'id_bool': {'id': True}, 'id_str': {'id': '7'}, 'id_huge': {'id': 10 ** 30}, 'id_neg': {'id': -1},
    'prio_str': {'priority': 'high'}, 'prio_huge': {'priority': 10 ** 30}, 'enable_str': {'enable': 'no'},
    'impl_int': {'implementation': 5, 'definitionName': ['x']},
    'value_dict': {'fields': [{'name': 'baseUrl', 'value': {'a': 1}}, {'name': 'apiKey', 'value': [1]}]},
    'apipath_int': {'fields': [{'name': 'baseUrl', 'value': 'https://t.qa.example'}, {'name': 'apiPath', 'value': 5}]},
    'url_ctl': {'fields': [{'name': 'baseUrl', 'value': 'https://t.qa.example/\r\nX-Evil: 1'}]},
    'url_huge': {'fields': [{'name': 'baseUrl', 'value': 'https://t.qa.example/' + 'a' * 100000}]},
    'caps_none': {'capabilities': None}, 'fields_dict': {'fields': {'baseUrl': 'x'}},
}


@pytest.mark.parametrize('mode', sorted(SHAPES))
def test_a_hostile_indexer_shape_is_never_a_500(owner, env, mode):  # noqa: F811
    env['fake'].data = [dict(GOOD, **SHAPES[mode])]
    r = _post(owner, '/api/trackers/import/prowlarr/preview')
    assert r.status_code in (200, 502), (r.status_code, r.get_data(as_text=True))
    if r.status_code == 502:
        assert r.get_json() == {'error': tracker_import.UNEXPECTED}
    assert 'qa-h-key' not in r.get_data(as_text=True)
    assert env['store'].list() == []


@pytest.mark.parametrize('top', [{'indexers': []}, 5, 'x', None, True])
def test_a_top_level_that_is_not_a_list_is_an_unexpected_response(owner, env, top):  # noqa: F811
    env['fake'].data = top
    r = _post(owner, '/api/trackers/import/prowlarr/preview')
    assert r.status_code == 502 and r.get_json() == {'error': tracker_import.UNEXPECTED}


def test_a_crash_inside_the_preview_is_a_502(owner, env, monkeypatch):  # noqa: F811
    def boom(*a, **k):
        raise TypeError('something nobody expected')
    monkeypatch.setattr(tracker_import, '_rows', boom)
    r = _post(owner, '/api/trackers/import/prowlarr/preview')
    assert r.status_code == 502 and r.get_json() == {'error': tracker_import.UNEXPECTED}


def test_the_good_shapes_still_import(owner, env):  # noqa: F811
    env['fake'].data = [GOOD]
    r = _post(owner, '/api/trackers/import/prowlarr/preview')
    assert r.status_code == 200
    (row,) = r.get_json()['indexers']
    assert row['state'] == 'ready' and row['type'] == 'torznab'


# ------------------------------------------------------------------ FF-B: one total deadline (real sockets)

class Slow(BaseHTTPRequestHandler):
    mode = 'fast'
    served = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Slow.served.append(self.path)
        try:
            if Slow.mode == 'fast':
                body = json.dumps([GOOD]).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif Slow.mode == 'body':                    # one byte at a time, forever
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'[')
                for _ in range(400):
                    self.wfile.write(b' ')
                    self.wfile.flush()
                    time.sleep(0.05)
            elif Slow.mode == 'headers':                 # the status line and headers trickle in
                for ch in b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Pad: ' + b'a' * 400:
                    self.wfile.write(bytes([ch]))
                    self.wfile.flush()
                    time.sleep(0.05)
            elif Slow.mode in ('silent', 'silent11'):                  # headers, then nothing
                self.send_response(200)
                self.send_header('Content-Length', '100')
                self.end_headers()
                self.wfile.flush()
                time.sleep(20)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


@pytest.fixture
def loopback(env, monkeypatch):  # noqa: F811
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Slow)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f'http://127.0.0.1:{srv.server_address[1]}'
    env['settings']['indexer.base_url'] = base
    monkeypatch.setattr(tracker_import, '_session_factory', None)      # the real requests.Session
    monkeypatch.setattr(tracker_import, 'TOTAL_S', 1.0)
    Slow.served = []
    yield base
    srv.shutdown()
    srv.server_close()


def test_a_real_fast_prowlarr_still_reads(owner, loopback):  # noqa: F811
    Slow.mode = 'fast'
    r = _post(owner, '/api/trackers/import/prowlarr/preview')
    assert r.status_code == 200, r.get_json()
    assert [x['state'] for x in r.get_json()['indexers']] == ['ready']
    assert Slow.served == ['/api/v1/indexer']


@pytest.mark.parametrize('mode', ['body', 'headers', 'silent', 'silent11'])
def test_a_slow_prowlarr_is_cut_at_the_total_deadline(owner, loopback, mode, monkeypatch):  # noqa: F811
    Slow.mode = mode
    monkeypatch.setattr(Slow, 'protocol_version', 'HTTP/1.1' if mode == 'silent11' else 'HTTP/1.0')
    t0 = time.monotonic()
    r = _post(owner, '/api/trackers/import/prowlarr/preview')
    took = time.monotonic() - t0
    assert r.status_code == 502 and r.get_json() == {'error': tracker_import.UNREACHABLE}
    assert took < 3.0, took                       # TOTAL_S is 1 s here; 20 s in the app
    assert len(Slow.served) == 1                  # one GET, no retry


def test_the_app_deadline_is_twenty_seconds_overall():
    assert tracker_import.TOTAL_S == 20 and tracker_import.TIMEOUT_S == 20


def test_no_socket_is_left_open_after_a_cut(owner, loopback):  # noqa: F811
    Slow.mode = 'body'
    before = threading.active_count()
    for _ in range(3):
        assert _post(owner, '/api/trackers/import/prowlarr/preview').status_code == 502
    time.sleep(0.2)
    assert threading.active_count() <= before + 4           # watchdog timers ended (server threads aside)
