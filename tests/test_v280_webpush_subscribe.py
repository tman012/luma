"""v280: Settings > Notifications Web Push subscribe UX on MINIMAL=1."""
from __future__ import annotations

from pathlib import Path

from app import minimal


ROOT = Path(__file__).resolve().parents[1]


def test_minimal_allowlists_notifications_page_and_push_apis():
    assert minimal.path_allowed('/settings/notifications', 'GET')
    assert minimal.path_allowed('/api/push/vapid-key', 'GET')
    assert minimal.path_allowed('/api/push/subscribe', 'POST')
    assert minimal.path_allowed('/api/push/unsubscribe', 'POST')
    assert minimal.path_allowed('/api/push/test', 'POST')
    assert minimal.path_allowed('/api/settings/connectors/webpush', 'GET')
    assert minimal.path_allowed('/api/settings/connectors/webpush', 'PUT')
    assert minimal.path_allowed('/api/settings/connectors/webpush/test', 'POST')
    assert not minimal.path_allowed('/api/settings/connectors/telegram', 'GET')


def test_settings_nav_has_notifications_not_under_connectors_only():
    src = (ROOT / 'frontend/src/pages/LibrariesPage.tsx').read_text()
    assert "slug: 'notifications', label: 'Notifications'" in src
    assert 'NotificationsSettings' in src
    connectors = (ROOT / 'frontend/src/components/home/ConnectorsSettings.tsx').read_text()
    assert 'WebPushConnector' not in connectors


def test_webpush_connector_has_enable_notifications_subscribe():
    src = (ROOT / 'frontend/src/components/home/WebPushConnector.tsx').read_text()
    assert 'Enable Notifications' in src
    assert 'webpush-enable-notifications' in src
    assert 'usePushSubscription' in src
    assert 'anyLayout: true' in src


def test_minimal_home_mounts_mobile_push_banner():
    src = (ROOT / 'frontend/src/pages/MinimalHomePage.tsx').read_text()
    assert 'MobilePushBanner' in src


def test_sw_is_above_v279():
    """v280 tip asserted v280; v281+ keeps SW above the pre-subscribe baseline."""
    import re
    sw = (ROOT / 'static/sw.js').read_text()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 280
