"""errtext follow-up: helper modules and job states keep exception text out of client
data (fixed messages, detail logged redacted), and every /api JSON reply is redacted."""
import re
from pathlib import Path

import pytest
import requests as _rq

import app.main

SECRET = 'SECRETKEY123'
LEAK = f"HTTPConnectionPool(host='h'): Max retries exceeded with url: /x?apikey={SECRET} X-Plex-Token={SECRET}"


def _clean(obj):
    text = repr(obj)
    for bad in (SECRET, 'apikey', 'HTTPConnectionPool', 'Max retries'):
        assert bad not in text, (bad, text)


def _boom(*a, **k):
    raise _rq.exceptions.ConnectionError(LEAK)


# ── connectors (ha_service, adb_utils, wol) ──────────────────────────
def test_connectors_hide_exception(monkeypatch):
    from app import ha_service, adb_utils, wol
    monkeypatch.setattr(ha_service.requests, 'post', _boom)
    r = ha_service.call_service('http://ha', 'tok', 'light', 'turn_on', {})
    assert r['ok'] is False and r['detail'] == 'Cannot connect to HA'
    _clean(r)

    class S:
        def __init__(self, *a): pass
        def settimeout(self, t): pass
        def connect(self, addr): raise OSError(LEAK)
        def setsockopt(self, *a): pass
        def sendto(self, *a): raise OSError(LEAK)
        def close(self): pass
    monkeypatch.setattr(adb_utils.socket, 'socket', S)
    monkeypatch.setattr(wol.socket, 'socket', S)
    r = adb_utils.tcp_probe('10.0.0.9', 5555)
    assert r == {'ok': False, 'detail': 'Host unreachable', 'error_type': 'unreachable'}
    r = wol.send_wol('aa:bb:cc:dd:ee:ff')
    assert r['ok'] is False and r['detail'] == 'WoL send failed'


# ── automation cycles (trending / anticipated watchlists) ───────────
def test_watchlist_cycle_hides_exception(monkeypatch):
    from app import trending_watchlist as tw

    class Settings:
        def get(self, k, d=None):
            return {'radarr.url': 'http://r', 'radarr.api_key': 'k', 'apis.trakt.enabled': True,
                    'apis.trakt.client_id': 'c'}.get(k, d)

    class Radarr:
        def __init__(self, *a): pass
        def get_quality_profiles(self): return [{'id': 1}]
        def get_root_folders(self): return [{'path': '/m'}]
    monkeypatch.setattr(tw.requests, 'get', _boom)
    r = tw._do_cycle(Settings(), None, Radarr)
    assert r['error'] == 'Trakt fetch failed'
    _clean(r)


# ── job states (library_mover / movie_import remote wraps) ───────────
def test_mover_job_error_is_fixed_text(monkeypatch):
    from app import library_mover as lm, remote_mover
    monkeypatch.setattr(remote_mover, 'remote_temp_for', lambda d: d + '.part')
    monkeypatch.setattr(lm, '_inflight_of', lambda h: {})
    monkeypatch.setattr(lm, '_set_inflight', lambda *a, **k: None)

    def refuse(*a, **k): raise remote_mover.RemoteMoverError(LEAK)
    monkeypatch.setattr(remote_mover, 'transfer', refuse)
    with pytest.raises(lm.Refused) as ei:
        lm._remote_transfer('/d/a.mkv', '/m/a.mkv')
    assert str(ei.value) == 'remote copy refused'

    def cut(*a, **k): raise remote_mover.RemoteTransient(LEAK)
    monkeypatch.setattr(remote_mover, 'transfer', cut)
    with pytest.raises(lm.Transient) as ei:
        lm._remote_transfer('/d/a.mkv', '/m/a.mkv')
    assert str(ei.value) == 'remote copy cut off'


# ── service clients (radarr_api, request_portal, voice_service) ─────
def test_service_clients_hide_exception(monkeypatch):
    from app.radarr_api import RadarrAPI
    from app import voice_service
    r = RadarrAPI('http://radarr', 'k')
    monkeypatch.setattr(r.session, 'post', _boom)
    monkeypatch.setattr(r.session, 'get', _boom)
    out = r.grab_release('g', 1)
    assert out == {'ok': False, 'error': 'Radarr request failed'}

    class Plex:
        def hub_search(self, *a, **k): raise RuntimeError(LEAK)
    out = voice_service._execute_tool('search_library', {'query': 'x'}, Plex(), None, None)
    assert out == {'error': 'Tool failed'}


# ── caches (library_cache) ──────────────────────────────────────────
def test_cache_status_hides_exception(monkeypatch):
    from app import library_cache as lc

    cache = lc._get_library_cache()                       # built first, then its DB reads fail

    def bad(*a, **k): raise RuntimeError(LEAK)
    monkeypatch.setattr(lc.sqlite3, 'connect', bad)
    out = cache.get_browse_library('Movies')
    assert out['error'] == 'Library unavailable'
    _clean(out)


# ── backstop: a 200 success body is redacted too ────────────────────
def test_success_body_with_secret_is_redacted(test_app):
    from flask import jsonify
    with test_app.test_request_context('/api/anything'):
        resp = jsonify({'ok': True, 'poster': f'http://x/p.jpg?api_key={SECRET}',
                        'h': f'Authorization: Bearer {SECRET}', 'title': 'Dune'})
        out = app.main._redact_api_error_bodies(resp)
        body = out.get_data(as_text=True)
    assert out.status_code == 200 and SECRET not in body and '"title":"Dune"' in body.replace(' ', '')
    with test_app.test_request_context('/api/anything'):
        plain = jsonify({'ok': True, 'title': 'Dune'})
        before = plain.get_data()
        assert app.main._redact_api_error_bodies(plain).get_data() == before   # untouched byte for byte


# ── static: no helper or route hands str(e) / f'{e}' to client data ─
ALLOWED = {
    # app-authored, safe-to-show messages (fixed constants or domain errors)
    'main.py': ["return jsonify({'error': str(e)}), 409", "return jsonify({'error': str(e)}), 400",
                "return jsonify({'error': str(exc)}), exc.status"],
    'start_flow.py': ["return jsonify({'error': str(e)}), 400", "return jsonify({'error': str(e)}), 409"],
    'tv_library_routes.py': ["return _no_store(jsonify({'error': str(e)}), 502)"],
    'tv_grab.py': ["'error': str(e), 'link': True"], 'movie_scanner.py': ["'error': str(e), 'link': True"],
    'automation/wym_routes.py': ['isinstance(e, daily_missing.PoolError)'],
    # job states from app domain exceptions (Refused/Transient/Pending/Skip/PoolError/TmdbError): their
    # text is app-authored; the raw remote-mover text is replaced at the wrap (library_mover, movie_import)
    'library_mover.py': ["error=str(e)"], 'movie_import.py': ["error=str(e)"], 'tv_downloads.py': ["error=str(e)"],
    'daily_missing.py': ["_state['last_error'] = str(e)"], 'tv_scan.py': ["_state['error'] = str(e)"],
    'import_reconciler.py': ['{err}'],                   # err = a job's stored (already fixed) error
    'paths.py': ['problems.append('],                     # startup self-check, printed to the server console
    'tv_store.py': ['_CORRUPT_TEXT'],                     # internal classification, never returned
}
CLIENT = re.compile(r"(return\b.*|['\"](error|message|detail|reason|stderr)['\"]\s*:.*|\berror\s*=.*|_last_error\s*=.*|"
                    r"\.append\(.*|\[['\"](error|last_error)['\"]\]\s*=.*)"
                    r"(str\((e|exc|err)\)|\{(e|exc|err)(!r|!s)?\}|repr\((e|exc)\))")


def test_static_no_exception_text_reaches_clients():
    root = Path(app.main.__file__).parent
    bad = []
    for p in root.rglob('*.py'):
        rel = str(p.relative_to(root))
        for n, ln in enumerate(p.read_text().splitlines(), 1):
            s = ln.strip()
            if s.startswith(('logger.', 'log.', '#')) or 'logger.' in s or not CLIENT.search(s):
                continue
            if any(a in s for a in ALLOWED.get(rel, [])):
                continue
            bad.append(f'{rel}:{n}: {s}')
    assert bad == [], '\n'.join(bad)
