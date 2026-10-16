"""Fail fast when a TLS handshake reaches the plain-HTTP gunicorn port.

Browsers often try https:// first on a typed address. Gunicorn's HTTP parser
would wait for a CRLF that a TLS ClientHello never sends, so the browser sat
on a blank page until the worker timeout. This gthread worker peeks at the
first bytes of a new connection and closes it at once when they look like a
TLS record (0x16 0x03), so the handshake fails immediately and the browser
can fall back to http://. Normal HTTP (and keep-alive) is untouched, and when
gunicorn itself serves HTTPS (certfile set) the check is skipped.
"""

import socket

from gunicorn.workers.gthread import ThreadWorker

TLS_HANDSHAKE = 0x16
TLS_MAJOR = 0x03


def looks_like_tls(head: bytes) -> bool:
    if not head or head[0] != TLS_HANDSHAKE:
        return False
    return len(head) < 2 or head[1] == TLS_MAJOR


class TLSRejectingThreadWorker(ThreadWorker):
    """gthread worker that drops TLS ClientHellos sent to a plain-HTTP port."""

    def handle(self, conn):
        # enqueue_req() runs conn.init() before handle(), so track the first
        # request on this connection ourselves.
        if not self.cfg.is_ssl and not getattr(conn, '_luma_tls_checked', False):
            conn._luma_tls_checked = True
            try:
                # Called only once the socket is readable, so this never blocks.
                head = conn.sock.recv(3, socket.MSG_PEEK | socket.MSG_DONTWAIT)
            except (BlockingIOError, InterruptedError):
                head = b''
            except OSError:
                return (False, conn)
            if looks_like_tls(head):
                self.log.debug('Closing TLS handshake sent to plain-HTTP port from %s',
                               conn.client)
                return (False, conn)
        return super().handle(conn)
