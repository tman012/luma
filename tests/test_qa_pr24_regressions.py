"""QA pins for PR #24 (Reviewer items 1, 2, 4, 5). Real sockets where the
behavior depends on the network stack (TLS, trickle); fake DNS elsewhere."""

import datetime
import socket
import ssl
import threading
import time

import pytest
# Imported at collection: tests that mock.patch.dict(sys.modules) drop any
# cryptography submodule first imported inside them, and a re-import then
# mismatches the Rust bindings ("curve must be an EllipticCurve instance").
from cryptography import x509  # noqa: F401
from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: F401
from cryptography.x509.oid import NameOID  # noqa: F401

import app.main
import app.user_auth as user_auth
from app import outbound_guard as og
from tests.test_downloads_add_ssrf import (  # noqa: F401  (fixtures)
    net, qbit, client, _post, LAN, REMOTE, ORIGIN, PUBLIC, TORRENT)


# ── (1) signed-out LAN under auth_required=disabled_local ───────────────

def test_signed_out_lan_disabled_local_is_implicit_owner_but_guarded(client, net, qbit):
    """PIN (PM to decide): a signed-out LAN client gets implicit owner, so it
    may add downloads; the outbound guard still refuses metadata/LAN links."""
    user_auth.create_user('owner', '1234', is_admin=True)
    app.main.settings.update({'security': {'auth_required': 'disabled_local'}})
    r = _post(client, 'http://169.254.169.254/latest/meta-data/', env=LAN)
    assert r.status_code == 400 and r.get_json()['error'] == 'Link not allowed'
    assert net.opens == []
    net.dns['tracker.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'tracker.example', '/t')] = (200, {}, TORRENT)
    r = _post(client, 'https://tracker.example/t', env=LAN)
    assert r.status_code == 200, r.get_json()
    assert len(qbit.added) == 1


def test_signed_out_remote_disabled_local_is_401(client, net, qbit):
    user_auth.create_user('owner', '1234', is_admin=True)
    app.main.settings.update({'security': {'auth_required': 'disabled_local'}})
    assert _post(client, 'http://169.254.169.254/', env=REMOTE).status_code == 401
    assert net.lookups == [] and qbit.logins == 0


# ── (2) second admin and the service API key ────────────────────────────

def _public(net):
    net.dns['tracker.example'] = [[PUBLIC]]
    net.routes[(PUBLIC, 'tracker.example', '/t')] = (200, {}, TORRENT)


def test_second_admin_may_add_and_is_guarded(client, net, qbit):
    """PIN: the route is admin-only (_require_admin), not owner-only as the
    commit message says. A second admin can add, through the same guard."""
    app.main.settings.update({'security': {'auth_required': 'enabled'}})
    user_auth.create_user('owner', '1234', is_admin=True)
    a2 = user_auth.create_user('admin2', '5678', is_admin=True)
    client.set_cookie('luma_auth_session', user_auth.create_session(a2['id']))
    assert _post(client, 'http://127.0.0.1:5001/', env=REMOTE).status_code == 400
    assert net.opens == []
    _public(net)
    assert _post(client, 'https://tracker.example/t', env=REMOTE).status_code == 200
    assert len(qbit.added) == 1


def test_service_api_key_may_add_and_is_guarded(client, net, qbit):
    """PIN: X-API-Key is treated as an admin here (same as other admin routes)."""
    app.main.settings.update({'security': {'auth_required': 'enabled'}})
    user_auth.create_user('owner', '1234', is_admin=True)
    hk = {'X-API-Key': app.main._SERVICE_API_KEY, **ORIGIN}
    r = client.post('/api/downloads/add', json={'torrent_url': 'http://169.254.169.254/'}, headers=hk,
                    environ_base=REMOTE)
    assert r.status_code == 400 and net.opens == []
    _public(net)
    r = client.post('/api/downloads/add', json={'torrent_url': 'https://tracker.example/t'}, headers=hk,
                    environ_base=REMOTE)
    assert r.status_code == 200 and len(qbit.added) == 1
    r = client.post('/api/downloads/add', json={'torrent_url': 'https://tracker.example/t'},
                    headers={'X-API-Key': 'wrong', **ORIGIN}, environ_base=REMOTE)
    assert r.status_code == 401


# ── real local servers (loopback allowed only inside these tests) ───────

def _serve(handler, tls_ctx=None):
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(5)
    seen = []

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            if tls_ctx:
                try:
                    c = tls_ctx.wrap_socket(c, server_side=True)
                except (ssl.SSLError, OSError):
                    seen.append('handshake-failed')
                    c.close()
                    continue
            threading.Thread(target=handler, args=(c, seen), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return srv, srv.getsockname()[1], seen


def _allow_loopback(monkeypatch, names):
    real = socket.getaddrinfo

    def gai(host, port, *a, **k):
        if host in names:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]
        return real(host, port, *a, **k)
    monkeypatch.setattr(og.socket, 'getaddrinfo', gai)
    monkeypatch.setattr(og, 'is_blocked_ip', lambda ip: False)


# ── (4) trickle-feeding upstream ─────────────────────────────────────────

def _drip(c, seen):
    c.recv(4096)
    seen.append('request')
    c.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/x-bittorrent\r\n\r\nd')
    try:
        for _ in range(60):
            time.sleep(0.25)
            c.sendall(b'0')
    except OSError:
        pass
    finally:
        c.close()


# Was xfail(strict) until outbound_guard.fetch got its total deadline.
def test_trickle_upstream_cut_by_total_deadline(monkeypatch):
    _allow_loopback(monkeypatch, {'drip.test'})
    srv, port, seen = _serve(_drip)
    t = time.monotonic()
    try:
        with pytest.raises(og.FetchError):
            og.fetch(f'http://drip.test:{port}/x', read_timeout=1.0, connect_timeout=1.0)
    finally:
        took = time.monotonic() - t
        srv.close()
    assert seen == ['request']
    assert took < 5.0, f'held for {took:.1f}s with a 1s read timeout'


# ── (5) TLS name mismatch ────────────────────────────────────────────────

def _certs(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'qa test ca')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'right.test')]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(2).not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('right.test')]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    (tmp_path / 'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (tmp_path / 'leaf.pem').write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (tmp_path / 'leaf.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                          serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / 'leaf.pem', tmp_path / 'leaf.key')
    return ctx


def _torrent(c, seen):
    # Under TLS 1.3 the server finishes its handshake before the client
    # checks the certificate, so a client that rejects the name closes the
    # connection with nothing sent: recv returns b'' (or raises). Only a read
    # with data is a request.
    try:
        try:
            data = c.recv(4096)
        except (ssl.SSLError, OSError):
            return
        if not data:
            return
        seen.append('request')
        c.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n' % len(TORRENT) + TORRENT)
    finally:
        c.close()


@pytest.mark.parametrize('host,ok', [('right.test', True), ('wrong.test', False)])
def test_tls_name_mismatch_is_502_and_nothing_added(client, qbit, monkeypatch, tmp_path, host, ok):
    ctx = _certs(tmp_path)
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'ca.pem'))
    _allow_loopback(monkeypatch, {'right.test', 'wrong.test'})
    srv, port, seen = _serve(_torrent, ctx)
    app.main.settings.update({'security': {'auth_required': 'enabled'}})
    user_auth.create_user('owner', '1234', is_admin=True)
    client.set_cookie('luma_auth_session',
                      user_auth.create_session(user_auth.get_user_by_name('owner')['id']))
    try:
        r = _post(client, f'https://{host}:{port}/t.torrent', env=REMOTE)
    finally:
        srv.close()
    if ok:   # positive control: same server, right name, trusted CA
        assert r.status_code == 200, r.get_json()
        assert seen == ['request'] and len(qbit.added) == 1
    else:
        assert r.status_code == 502, r.get_json()
        assert 'request' not in seen and qbit.added == []


# ── (4b) Reviewer F1: trickled status line / headers are cut too ────────

def _header_drip(c, seen):
    c.recv(4096)
    seen.append('request')
    try:
        c.sendall(b'HTTP/1.1 200 OK\r\n')
        for i in range(200):            # one header byte every 0.2 s, never ending
            time.sleep(0.2)
            c.sendall(b'X')
    except OSError:
        pass
    finally:
        c.close()


def _status_drip(c, seen):
    c.recv(4096)
    seen.append('request')
    try:
        for b in b'HTTP/1.1 200 OK' * 20:
            time.sleep(0.2)
            c.sendall(bytes([b]))
    except OSError:
        pass
    finally:
        c.close()


@pytest.mark.parametrize('handler', [_header_drip, _status_drip])
def test_header_trickle_cut_by_total_deadline(monkeypatch, handler):
    _allow_loopback(monkeypatch, {'drip.test'})
    srv, port, seen = _serve(handler)
    t = time.monotonic()
    try:
        with pytest.raises(og.FetchError) as e:
            og.fetch(f'http://drip.test:{port}/x', read_timeout=5.0, connect_timeout=5.0, total_timeout=1.5)
    finally:
        took = time.monotonic() - t
        srv.close()
    assert seen == ['request']
    assert str(e.value) == 'Request took too long'
    assert took < 3.0, f'held for {took:.1f}s with total_timeout=1.5'
