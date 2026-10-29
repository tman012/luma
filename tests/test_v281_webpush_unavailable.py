"""v281: Enable Notifications must not stay disabled as 'Enable Notifications' when unsupported."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_webpush_unsupported_label_is_unavailable_not_enable():
    src = (ROOT / 'frontend/src/components/home/WebPushConnector.tsx').read_text()
    # Settings premium pass: Blocked / Unavailable is a chip; only a supported, allowed browser gets 'Turn on'
    assert "subDenied || subUnsupported ? (" in src
    assert "{subDenied ? 'Blocked' : 'Unavailable'}" in src
    assert "'Turn on'" in src


def test_push_hook_sync_capability_avoids_stuck_unsupported():
    src = (ROOT / 'frontend/src/hooks/usePushSubscription.ts').read_text()
    assert 'function readPushCapability' in src
    assert 'function initialPushState' in src
    assert 'isSecureContext' in src
    assert 'useState<PushState>(initialPushState)' in src
    # Must not default first paint to unsupported when APIs exist
    assert "useState<PushState>('unsupported')" not in src


def test_sw_is_v281_or_above():
    import re
    sw = (ROOT / 'static/sw.js').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 281
