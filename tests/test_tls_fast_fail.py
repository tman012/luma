"""A TLS handshake on the plain-HTTP port fails fast; plain HTTP keeps working.

Starts the real gunicorn.conf.py (post_worker_init only creates the user
tables, so no background services start) on an ephemeral loopback port.
"""

import http.client
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.tls_guard import looks_like_tls

ROOT = Path(__file__).resolve().parent.parent


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('tlsff')
    conf = tmp / 'conf.py'
    conf.write_text(
        'import runpy\n'
        f'globals().update(runpy.run_path({str(ROOT / "gunicorn.conf.py")!r}))\n'
        'def post_worker_init(worker):\n'
        '    from app import user_auth\n'
        '    user_auth.init_db()\n'
    )
    port = _free_port()
    env = dict(os.environ)
    for var in ('LUMA_HTTPS_CERT', 'LUMA_HTTPS_KEY', 'LUMA_URL_BASE'):
        env.pop(var, None)
    env.update(LUMA_BIND='127.0.0.1', LUMA_PORT=str(port),
               LUMA_DATA_DIR=str(tmp / 'data'), LUMA_CACHE_DIR=str(tmp / 'cache'),
               HERO_CACHE_DIR=str(tmp / 'hero'), DATABASE_PATH=str(tmp / 'data' / 't.db'))
    for d in ('data', 'cache', 'hero'):
        (tmp / d).mkdir()
    proc = subprocess.Popen(
        [sys.executable, '-m', 'gunicorn', '-c', str(conf), 'app.main:app'],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail('gunicorn exited: ' + proc.stderr.read().decode(errors='replace')[-2000:])
        try:
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            c.request('GET', '/login')
            c.getresponse().read()
            c.close()
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail('gunicorn did not start')
    yield port
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _tls_attempt(port):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    start = time.monotonic()
    with socket.create_connection(('127.0.0.1', port), timeout=5) as raw:
        with pytest.raises((ssl.SSLError, OSError)):
            ctx.wrap_socket(raw, server_hostname='localhost')
    return time.monotonic() - start


def _get(port, path='/sw.js', conn=None):
    c = conn or http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    c.request('GET', path)
    r = c.getresponse()
    r.read()
    return c, r


def test_looks_like_tls():
    assert looks_like_tls(b'\x16\x03\x01')
    assert looks_like_tls(b'\x16')
    assert not looks_like_tls(b'GET / HTTP/1.1')
    assert not looks_like_tls(b'')
    assert not looks_like_tls(b'\x16\x02')


def test_worker_class_is_guarded():
    assert "worker_class = 'app.tls_guard.TLSRejectingThreadWorker'" in (
        ROOT / 'gunicorn.conf.py').read_text()


def test_tls_handshake_fails_fast(server):
    for _ in range(3):
        elapsed = _tls_attempt(server)
        assert elapsed < 0.1, f'TLS handshake took {elapsed:.3f}s to fail'


def test_plain_http_works_after_tls_attempt(server):
    _tls_attempt(server)
    start = time.monotonic()
    c, r = _get(server)
    assert r.status == 200
    assert time.monotonic() - start < 1
    c.close()


def test_keepalive_unaffected(server):
    c, r1 = _get(server)
    _tls_attempt(server)
    c, r2 = _get(server, conn=c)
    assert r1.status == 200 and r2.status == 200
    c.close()


@pytest.mark.parametrize('path', ['/', '/login', '/sw.js', '/manifest.json', '/does-not-exist'])
def test_no_hsts_header(server, path):
    c = http.client.HTTPConnection('127.0.0.1', server, timeout=5)
    c.request('GET', path, headers={'X-Forwarded-Proto': 'https'})
    r = c.getresponse()
    r.read()
    assert r.getheader('Strict-Transport-Security') is None
    c.close()


def test_no_hsts_in_source():
    for p in [ROOT / 'gunicorn.conf.py', *(ROOT / 'app').rglob('*.py')]:
        text = p.read_text(errors='ignore').lower()
        assert 'strict-transport-security' not in text.replace(
            'never add strict-transport-security', ''), p
