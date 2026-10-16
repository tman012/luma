#!/usr/bin/env python3
"""Container health check: GET <scheme>://<host>:<port><url base>/health.

Port, bind address, URL base and HTTPS are read the same way the server reads
them (app.server_config with the saved settings), so the check follows any
change made in Settings. Exit code 0 when healthy, 1 otherwise.
"""

import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def health_url(settings) -> str:
    from app import server_config

    host = server_config.get_bind_address(settings)
    if host in ('', '0.0.0.0', '::', '[::]'):
        host = '127.0.0.1'
    elif ':' in host and not host.startswith('['):
        host = f'[{host}]'
    try:
        https = server_config.get_https_cert(settings) is not None
    except FileNotFoundError:
        https = False
    scheme = 'https' if https else 'http'
    port = server_config.get_port(settings)
    base = server_config.get_url_base(settings)
    return f'{scheme}://{host}:{port}{base}/health'


def main() -> int:
    from app.settings_manager import SettingsManager

    url = health_url(SettingsManager())
    ctx = ssl.create_default_context()
    # Local loopback check against Luma's own (often self-signed) certificate
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url, timeout=3, context=ctx) as resp:
            return 0 if resp.status == 200 else 1
    except Exception as exc:
        print(f'unhealthy: {url}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
