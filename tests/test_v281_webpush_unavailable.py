"""v281: Enable Notifications must not stay disabled as 'Enable Notifications' when unsupported."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_webpush_unsupported_label_is_unavailable_not_enable():
    src = (ROOT / 'frontend/src/components/home/WebPushConnector.tsx').read_text()
    assert "subUnsupported ? 'Unavailable'" in src
    assert 'Enable Notifications' in src
    # Must not fall through unsupported → Enable Notifications while disabled
    assert "subDenied ? 'Blocked' : subUnsupported ? 'Unavailable' : 'Enable Notifications'" in src


def test_push_hook_sync_capability_avoids_stuck_unsupported():
    src = (ROOT / 'frontend/src/hooks/usePushSubscription.ts').read_text()
    assert 'function readPushCapability' in src
    assert 'function initialPushState' in src
    assert 'isSecureContext' in src
    assert 'useState<PushState>(initialPushState)' in src
    # Must not default first paint to unsupported when APIs exist
    assert "useState<PushState>('unsupported')" not in src


def test_sw_is_v281():
    sw = (ROOT / 'static/sw.js').read_text()
    assert "CACHE_VERSION = 'media-kiosk-v281'" in sw
