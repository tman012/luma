"""gunicorn and the health check follow the saved settings (round 4, item 6)."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


class _Settings:
    def __init__(self, **kv):
        self.kv = kv

    def get(self, key, default=None):
        return self.kv.get(key, default)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ('LUMA_PORT', 'LUMA_BIND', 'LUMA_HTTPS_CERT', 'LUMA_HTTPS_KEY', 'LUMA_URL_BASE'):
        monkeypatch.delenv(var, raising=False)


def _load(path, monkeypatch, settings):
    import app.settings_manager
    monkeypatch.setattr(app.settings_manager, 'SettingsManager', lambda *a, **k: settings)
    spec = importlib.util.spec_from_file_location(f'_t_{path.stem}', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tls_pair(tmp_path):
    cert, key = tmp_path / 'c.pem', tmp_path / 'k.pem'
    cert.write_text('x')
    key.write_text('x')
    return str(cert), str(key)


def test_gunicorn_binds_saved_port_and_address(monkeypatch):
    conf = _load(ROOT / 'gunicorn.conf.py', monkeypatch,
                 _Settings(**{'general.port': 5311, 'general.bind': '127.0.0.1'}))
    assert conf.bind == '127.0.0.1:5311'
    assert (conf.certfile, conf.keyfile) == (None, None)


def test_gunicorn_uses_network_tab_port(monkeypatch):
    conf = _load(ROOT / 'gunicorn.conf.py', monkeypatch, _Settings(**{'network.flask_port': 5400}))
    assert conf.bind == '0.0.0.0:5400'


def test_gunicorn_env_still_wins(monkeypatch):
    monkeypatch.setenv('LUMA_PORT', '5999')
    monkeypatch.setenv('LUMA_BIND', '::1')
    conf = _load(ROOT / 'gunicorn.conf.py', monkeypatch, _Settings(**{'general.port': 5311}))
    assert conf.bind == '[::1]:5999'


def test_gunicorn_https_from_settings(monkeypatch, tls_pair):
    cert, key = tls_pair
    conf = _load(ROOT / 'gunicorn.conf.py', monkeypatch,
                 _Settings(**{'security.https_cert': cert, 'security.https_key': key}))
    assert (conf.certfile, conf.keyfile) == (cert, key)


def test_healthcheck_url_follows_settings(monkeypatch, tls_pair):
    hc = _load(ROOT / 'scripts/healthcheck.py', monkeypatch, _Settings())
    assert hc.health_url(_Settings()) == 'http://127.0.0.1:5001/health'
    cert, key = tls_pair
    s = _Settings(**{'general.port': 5311, 'general.url_base': 'luma/', 'general.bind': '192.0.2.5',
                     'security.https_cert': cert, 'security.https_key': key})
    assert hc.health_url(s) == 'https://192.0.2.5:5311/luma/health'


def test_healthcheck_reaches_running_server(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200 if self.path == '/luma/health' else 404)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = _Settings(**{'general.port': srv.server_port, 'general.url_base': '/luma'})
        hc = _load(ROOT / 'scripts/healthcheck.py', monkeypatch, s)
        assert hc.main() == 0
        assert seen == ['/luma/health']
        s.kv['general.url_base'] = ''
        assert hc.main() == 1
    finally:
        srv.shutdown()


def test_container_files_use_the_health_script():
    docker = (ROOT / 'Dockerfile').read_text()
    compose = (ROOT / 'docker-compose.yml').read_text()
    assert 'COPY gunicorn.conf.py' in docker and 'COPY scripts/healthcheck.py' in docker
    assert 'scripts/healthcheck.py' in docker.split('HEALTHCHECK', 1)[1]
    assert '/health"' not in compose and 'scripts/healthcheck.py' in compose
    assert 'Flask dev server' not in (ROOT / 'entrypoint.sh').read_text()
