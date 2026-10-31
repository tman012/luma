#!/usr/bin/env python3
"""Optional reference host helper (Ops owns production).

Ops FINAL (preferred):
  CLI:  /home/tbosley02/bin/luma-host-remote-mover.sh cp|mv SRC DEST
  Sock: host /home/tbosley02/luma-mover/mover.sock
        bind-mount → container /run/luma-mover/mover.sock
  HTTP over AF_UNIX:
    POST /move  {"mode":"cp|mv","src":"...","dest":"...","tmp":"<dest dir>/.luma-<hex16>.luma-partial"}
         v324: tmp is chosen by Luma (library_mover.partial_name()); passed as
         4th CLI arg. The CLI copies SRC→tmp, checks size, renames tmp→dest
         (no replace), removes tmp on failure, and adds NO suffix of its own.
         → {"ok": bool, "rc": N, "output": "..."}
         Optional "transfer_host": the library server's transfer address (Settings >
         Library paths). Passed to the CLI as env LUMA_TRANSFER_HOST (over SSH as a
         quoted VAR=value prefix); absent → the CLI runs exactly as before.
    GET  /health → {"ok": true}

TCP 127.0.0.1:18772 is HOST ONLY — containers must use the Unix socket
(LUMA_REMOTE_MOVER_SOCK=/run/luma-mover/mover.sock). Never 172.17.0.1.

This script can listen on a Unix socket for local/dev; production uses Ops' wrapper.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

SOCK = os.environ.get('LUMA_HOST_MOVER_SOCK', os.path.expanduser('~/luma-mover/mover.sock'))
CLI = os.environ.get(
    'LUMA_HOST_MOVER_CLI',
    os.path.expanduser('~/bin/luma-host-remote-mover.sh'))
DOWNLOADS_ROOT = os.environ.get('LUMA_REMOTE_DOWNLOADS_ROOT', '/scratch/downloads').rstrip('/')
MEDIA_ROOT = os.environ.get('LUMA_REMOTE_MEDIA_ROOT', '/media').rstrip('/')
TIMEOUT_S = int(os.environ.get('LUMA_REMOTE_MOVER_TIMEOUT_S', '3600'))
EXISTS = 17
TMP_RE = re.compile(r'^\.luma-[0-9a-f]{16}\.luma-partial$')


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + '/')


SSH_TARGET = os.environ.get('LUMA_HOST_MOVER_SSH', '').strip()          # user@host: run the remote CLI over SSH
REMOTE_CLI = os.environ.get('LUMA_HOST_MOVER_REMOTE_CLI', 'bin/luma-remote-mover.sh')   # relative: the remote home


def remote_command(argv) -> str:
    """The ONE string ssh hands the remote shell: every token shell-quoted (shlex.join), so spaces,
    parentheses and quotes in a path ('Smile 2 (2024)') reach the remote CLI as one argument each.
    (OpenSSH joins its argv with spaces and the remote shell parses it again: never pass paths unquoted.)"""
    return shlex.join([str(a) for a in argv])


TRANSFER_ENV = 'LUMA_TRANSFER_HOST'
_DNS_RE = re.compile(r'(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
                     r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$')


def valid_transfer_host(host: str) -> bool:
    """Same rule as Luma's file_management.valid_address: an IPv4 literal (not loopback, link-local,
    multicast or unspecified) or a DNS name whose labels start alphanumeric and are not hex numbers.
    No IPv6, never a leading '-' (no option injection downstream), no user@, brackets, ports, spaces
    or shell characters."""
    import ipaddress
    if not isinstance(host, str) or not host or len(host) > 253:
        return False
    if re.fullmatch(r'[0-9.]+', host):
        try:
            ip = ipaddress.IPv4Address(host)
        except ValueError:
            return False   # a broken or non-canonical IPv4 (leading zeros, too few octets)
        return not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                    or ip.is_reserved or ip.packed[0] == 0)   # 240/4 incl. 255.255.255.255 and 0/8, as the shell
    # IPv4 or DNS only (parity with the Ops mover): no IPv6, no ':' at all. A label that is a hex
    # number ('0x7f000001', '0x7f.1') is refused: inet_aton would read it as an IP.
    if not _DNS_RE.fullmatch(host):
        return False
    return not any(re.fullmatch(r'0x[0-9a-f]*', label, re.IGNORECASE) for label in host.split('.'))


def build_cmd(mode: str, src: str, dest: str, tmp: str = '', transfer_host: str = '') -> list:
    """argv for subprocess (never a shell): the local CLI, or ssh with one quoted remote command."""
    args = [mode, src, dest] + ([tmp] if tmp else [])
    if SSH_TARGET:
        remote = remote_command([REMOTE_CLI, *args])
        if transfer_host and valid_transfer_host(transfer_host):
            remote = f'{TRANSFER_ENV}={shlex.quote(transfer_host)} {remote}'
        return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', SSH_TARGET, '--', remote]
    return [CLI, *args]


def run_move(mode: str, src: str, dest: str, tmp: str = '', transfer_host: str = '') -> tuple[int, str]:
    if mode not in ('cp', 'mv'):
        return 2, f'mode must be cp|mv, got {mode!r}'
    if not _under(src, DOWNLOADS_ROOT):
        return 2, f'src must be under {DOWNLOADS_ROOT}'
    if not _under(dest, MEDIA_ROOT):
        return 2, f'dest must be under {MEDIA_ROOT}'
    if tmp and (os.path.dirname(tmp) != os.path.dirname(dest) or not TMP_RE.match(os.path.basename(tmp))):
        return 2, 'tmp must be <dest dir>/.luma-<hex16>.luma-partial'
    if transfer_host and not valid_transfer_host(transfer_host):
        return 2, 'transfer_host must be an IP address or host name'
    cmd = build_cmd(mode, src, dest, tmp, transfer_host)
    env = None
    if transfer_host:
        env = dict(os.environ, **{TRANSFER_ENV: transfer_host})
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, check=False, env=env)
    except FileNotFoundError:
        return 127, f'CLI not found: {CLI}'
    except subprocess.TimeoutExpired:
        return 124, f'timed out after {TIMEOUT_S}s'
    out = ((p.stdout or '') + (p.stderr or '')).strip()
    return p.returncode, out


class Handler(BaseHTTPRequestHandler):
    server_version = 'luma-remote-mover-host/2'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == '/health':
            self._json(200, {'ok': True})
            return
        self._json(404, {'ok': False, 'rc': 404, 'output': 'not found'})

    def do_POST(self):
        if urlparse(self.path).path != '/move':
            self._json(404, {'ok': False, 'rc': 404, 'output': 'not found'})
            return
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            data = json.loads(raw.decode() or '{}')
        except json.JSONDecodeError:
            self._json(400, {'ok': False, 'rc': 2, 'output': 'invalid json'})
            return
        # Prefer "mode"; accept legacy "op".
        mode = str(data.get('mode') or data.get('op') or 'cp').strip()
        src = str(data.get('src') or '').strip()
        dest = str(data.get('dest') or '').strip()
        if not src or not dest:
            self._json(400, {'ok': False, 'rc': 2, 'output': 'src and dest required'})
            return
        tmp = str(data.get('tmp') or '').strip()
        transfer_host = str(data.get('transfer_host') or '').strip()
        rc, output = run_move(mode, src, dest, tmp, transfer_host)
        ok = rc == 0
        code = 200 if ok else (409 if rc == EXISTS else 500)
        self._json(code, {'ok': ok, 'rc': rc, 'output': output})


class ThreadingUnixServer(ThreadingMixIn, object):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, sock_path: str, handler):
        self.address_family = socket.AF_UNIX
        Path(sock_path).parent.mkdir(parents=True, exist_ok=True)
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        self.server_address = sock_path
        self.RequestHandlerClass = handler
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(sock_path)
        os.chmod(sock_path, 0o660)
        self.socket.listen(32)
        self.__is_shut_down = __import__('threading').Event()
        self.__shutdown_request = False

    def serve_forever(self, poll_interval=0.5):
        self.__is_shut_down.clear()
        try:
            while not self.__shutdown_request:
                r = __import__('select').select([self.socket], [], [], poll_interval)[0]
                if r:
                    self._handle_request_noblock()
        finally:
            self.__shutdown_request = False
            self.__is_shut_down.set()

    def _handle_request_noblock(self):
        try:
            request, client_address = self.socket.accept()
        except OSError:
            return
        try:
            self.RequestHandlerClass(request, client_address, self)
        finally:
            try:
                request.close()
            except Exception:
                pass

    def fileno(self):
        return self.socket.fileno()

    def server_close(self):
        try:
            self.socket.close()
        finally:
            if os.path.exists(self.server_address):
                os.unlink(self.server_address)


def main() -> int:
    if not Path(CLI).is_file():
        sys.stderr.write(f'WARN: CLI not found: {CLI}\n')
    httpd = ThreadingUnixServer(SOCK, Handler)
    sys.stderr.write(f'listening unix:{SOCK}  CLI={CLI}\n')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write('\nbye\n')
    finally:
        httpd.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
