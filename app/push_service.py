#!/usr/bin/env python3
"""
Web Push notification service.

Manages VAPID key generation, subscription storage, and sending push
notifications to subscribed browsers.  Sending is fire-and-forget on a
daemon thread so webhook responses are never delayed.

Subscriptions are stored per-user as a dict keyed by str(user_id):
  {"0": [sub1, sub2], "3": [sub3]}
Legacy flat-list format is auto-migrated on first load.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Subscription storage (file-backed, thread-safe, per-user)
# ---------------------------------------------------------------------------
_SUBS_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
_SUBS_FILE = _SUBS_DIR / "push_subscriptions.json"
_subs_lock = threading.Lock()


def _load_subscriptions() -> dict[str, list[dict]]:
    """Read subscriptions from disk.

    Returns a dict keyed by user_id string.  If the file contains the
    legacy flat-list format, it is migrated to ``{"0": old_list}`` and
    saved immediately.
    """
    try:
        if _SUBS_FILE.exists():
            raw = json.loads(_SUBS_FILE.read_text())
            if isinstance(raw, list):
                # Legacy flat-list format → migrate to per-user dict
                migrated: dict[str, list[dict]] = {"0": raw}
                logger.info(f"Migrating {len(raw)} push subscriptions from flat list to per-user dict")
                _save_subscriptions(migrated)
                return migrated
            if isinstance(raw, dict):
                return raw
    except Exception as exc:
        logger.error(f"Failed to load push subscriptions: {exc}")
    return {}


def _save_subscriptions(subs: dict[str, list[dict]]) -> None:
    """Write subscriptions to disk."""
    try:
        _SUBS_DIR.mkdir(parents=True, exist_ok=True)
        _SUBS_FILE.write_text(json.dumps(subs, indent=2))
    except Exception as exc:
        logger.error(f"Failed to save push subscriptions: {exc}")


def add_subscription(user_id: int, info: dict) -> bool:
    """Store a PushSubscription JSON (upsert by endpoint within user's list)."""
    endpoint = info.get("endpoint", "")
    if not endpoint:
        return False
    key = str(user_id)
    with _subs_lock:
        subs = _load_subscriptions()
        user_list = subs.get(key, [])
        # Replace existing entry with same endpoint
        user_list = [s for s in user_list if s.get("endpoint") != endpoint]
        user_list.append(info)
        subs[key] = user_list
        _save_subscriptions(subs)
    total = sum(len(v) for v in subs.values())
    logger.info(f"Push subscription added/updated for user {user_id} ({total} total)")
    return True


def remove_subscription(endpoint: str) -> bool:
    """Remove a subscription by endpoint URL (searches all users)."""
    removed = 0
    with _subs_lock:
        subs = _load_subscriptions()
        for key in list(subs.keys()):
            before = len(subs[key])
            subs[key] = [s for s in subs[key] if s.get("endpoint") != endpoint]
            removed += before - len(subs[key])
        if removed:
            _save_subscriptions(subs)
    total = sum(len(v) for v in subs.values())
    logger.info(f"Push subscription removed ({removed} removed, {total} remaining)")
    return removed > 0


def get_subscription_count(user_id: int | None = None) -> int:
    """Return number of stored subscriptions.

    If *user_id* is given, return count for that user only.
    If None, return total across all users.
    """
    with _subs_lock:
        subs = _load_subscriptions()
        if user_id is not None:
            return len(subs.get(str(user_id), []))
        return sum(len(v) for v in subs.values())


def _all_subscriptions() -> list[dict]:
    """Return a flat list of ALL subscriptions across all users."""
    subs = _load_subscriptions()
    result = []
    for user_list in subs.values():
        result.extend(user_list)
    return result


def get_subscribed_user_ids() -> list[int]:
    """Return the user IDs that currently have at least one push subscription."""
    with _subs_lock:
        subs = _load_subscriptions()
    ids = []
    for k, v in subs.items():
        if v:
            try:
                ids.append(int(k))
            except (TypeError, ValueError):
                continue
    return ids


def _user_subscriptions(user_id: int) -> list[dict]:
    """Return subscriptions for a single user."""
    subs = _load_subscriptions()
    return list(subs.get(str(user_id), []))


# ---------------------------------------------------------------------------
# VAPID key management
# ---------------------------------------------------------------------------

def ensure_vapid_keys(settings_manager) -> tuple[str, str]:
    """Return (public_key, private_key), auto-generating on first call.

    Keys are stored in settings.json via the SettingsManager so they
    persist across Docker rebuilds (volume-mounted data dir).
    """
    pub = settings_manager.get("notifications.web_push.vapid_public_key") or ""
    priv = settings_manager.get("notifications.web_push.vapid_private_key") or ""

    if pub and priv:
        return pub, priv

    try:
        import base64
        from py_vapid import Vapid
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        vapid = Vapid()
        vapid.generate_keys()

        # Public key: uncompressed EC point (65 bytes) → URL-safe base64
        pub_bytes = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub = base64.urlsafe_b64encode(pub_bytes).rstrip(b'=').decode()

        # Private key: raw 32-byte scalar → URL-safe base64
        priv_val = vapid.private_key.private_numbers().private_value
        priv_bytes = priv_val.to_bytes(32, 'big')
        priv = base64.urlsafe_b64encode(priv_bytes).rstrip(b'=').decode()

        settings_manager.update({"notifications": {"web_push": {
            "vapid_public_key": pub,
            "vapid_private_key": priv,
        }}})
        logger.info("Generated new VAPID key pair")
        return pub, priv
    except Exception as exc:
        logger.error(f"Failed to generate VAPID keys: {exc}")
        return "", ""


# ---------------------------------------------------------------------------
# Send push notification (fire-and-forget)
# ---------------------------------------------------------------------------

def _do_send(title: str, body: str, tag: str, url: str,
             image: str,
             vapid_pub: str, vapid_priv: str, vapid_email: str,
             subs: list[dict],
             actions: Optional[list[dict]] = None,
             extra: Optional[dict] = None) -> None:
    """Worker that actually sends push messages. Runs on daemon thread."""
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.error("pywebpush not installed — cannot send push notifications")
        return

    data = {
        "title": title,
        "body": body,
        "tag": tag,
        "url": url or "/",
        "icon": "/static/images/ic_launcher_round.png",
        "badge": "/static/images/ic_launcher_round.png",
    }
    if image:
        data["image"] = image
    # Action buttons are rendered by Chrome/Android and ignored by iOS Safari,
    # so they are always additive — a notification carrying them must still make
    # sense as a plain tap. `extra` rides along in notification.data so the
    # service worker can act on an action press without opening the app.
    if actions:
        data["actions"] = actions
    if extra:
        data.update(extra)
    payload = json.dumps(data)

    stale_endpoints: list[str] = []

    for sub in subs:
        try:
            # IMPORTANT: pass a FRESH claims dict per send. pywebpush mutates the
            # dict in place (it stamps in the endpoint's `aud`), so reusing one
            # dict across subscriptions leaks the first endpoint's `aud` into the
            # next — which makes Apple Web Push reject every send after an FCM
            # one with 403 Forbidden.
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=vapid_priv,
                vapid_claims={"sub": vapid_email},
            )
        except WebPushException as exc:
            # NOTE: a requests.Response with a 4xx/5xx status is falsy
            # (Response.__bool__ returns .ok), so test `is not None` — using a
            # plain truthiness check silently drops the status code to 0 and
            # prevents stale subscriptions from ever being purged.
            resp = getattr(exc, "response", None)
            status_code = getattr(resp, "status_code", 0) if resp is not None else 0
            resp_body = ""
            try:
                resp_body = resp.text if resp is not None else ""
            except Exception:
                pass
            # Some failure details only surface in the exception string.
            exc_str = str(exc)
            mismatch = "VapidPkHashMismatch" in resp_body or "VapidPkHashMismatch" in exc_str

            if status_code in (404, 410):
                # 410 Gone / 404 Not Found → the push service permanently
                # invalidated this subscription. Purge it; the device creates a
                # fresh one next time it subscribes.
                stale_endpoints.append(sub.get("endpoint", ""))
                logger.info(f"Removing stale push subscription (HTTP {status_code})")
            elif mismatch:
                # Created with a different VAPID key — can never work, so purge.
                stale_endpoints.append(sub.get("endpoint", ""))
                logger.info("Removing stale push subscription (VAPID key mismatch)")
            else:
                # 403 and other errors are NOT auto-purged: they're usually a
                # fixable server-side VAPID/claims problem, not a dead device.
                endpoint = sub.get("endpoint", "")[:60]
                logger.warning(f"Push send failed ({status_code}) to {endpoint}: {resp_body or exc}")
        except Exception as exc:
            logger.warning(f"Push send error: {exc}")

    # Clean up stale subscriptions (find and remove from correct user list)
    if stale_endpoints:
        stale_set = set(stale_endpoints)
        with _subs_lock:
            current = _load_subscriptions()
            for key in list(current.keys()):
                current[key] = [s for s in current[key] if s.get("endpoint") not in stale_set]
            _save_subscriptions(current)
        logger.info(f"Cleaned {len(stale_endpoints)} stale push subscriptions")


def _prepare_vapid(settings_manager):
    """Resolve VAPID keys and email from settings. Returns (pub, priv, email) or None."""
    if settings_manager is None:
        try:
            from app.settings_manager import SettingsManager
            settings_manager = SettingsManager()
        except Exception:
            return None

    if not settings_manager.get("notifications.web_push.enabled"):
        return None

    vapid_pub, vapid_priv = ensure_vapid_keys(settings_manager)
    if not vapid_pub or not vapid_priv:
        return None

    vapid_email = settings_manager.get("notifications.web_push.vapid_email") or "mailto:admin@bosley.media"
    return vapid_pub, vapid_priv, vapid_email


def send_push_to_user(
    user_id: int,
    title: str,
    body: str,
    tag: str = "",
    url: str = "/",
    image: str = "",
    settings_manager=None,
    actions: Optional[list] = None,
    extra: Optional[dict] = None,
) -> bool:
    """Fire-and-forget push to a single user's subscribers. Returns True if dispatched."""
    vapid = _prepare_vapid(settings_manager)
    if not vapid:
        return False
    vapid_pub, vapid_priv, vapid_email = vapid

    with _subs_lock:
        subs = _user_subscriptions(user_id)

    if not subs:
        logger.debug(f"No push subscribers for user {user_id} — skipping")
        return False

    t = threading.Thread(
        target=_do_send,
        args=(title, body, tag, url, image, vapid_pub, vapid_priv, vapid_email, subs),
        kwargs={"actions": actions, "extra": extra},
        daemon=True,
    )
    t.start()
    logger.info(f"Push notification dispatched to user {user_id}: {title} → {len(subs)} subscriber(s)")
    return True


def send_push_notification(
    title: str,
    body: str,
    tag: str = "",
    url: str = "/",
    image: str = "",
    settings_manager=None,
) -> bool:
    """Fire-and-forget push to ALL subscribers (broadcast). Returns True if dispatched."""
    vapid = _prepare_vapid(settings_manager)
    if not vapid:
        return False
    vapid_pub, vapid_priv, vapid_email = vapid

    with _subs_lock:
        subs = _all_subscriptions()

    if not subs:
        logger.debug("No push subscribers — skipping")
        return False

    t = threading.Thread(
        target=_do_send,
        args=(title, body, tag, url, image, vapid_pub, vapid_priv, vapid_email, subs),
        daemon=True,
    )
    t.start()
    logger.info(f"Push notification dispatched: {title} → {len(subs)} subscriber(s)")
    return True
