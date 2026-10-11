"""
harmony_service.py — Direct Harmony Hub client via local WebSocket API.

Uses aioharmony to communicate directly with the Harmony Hub, eliminating
the Home Assistant middleman.

Architecture: a single background thread runs a persistent asyncio event
loop with a long-lived WebSocket connection per hub IP.  Public functions
submit work to that loop via run_coroutine_threadsafe() and block on the
future — no connect/disconnect overhead per call.  The connection auto-
reconnects if it drops and idles out after 5 minutes of inactivity.
"""

import asyncio
import logging
import threading
import time
from typing import Optional

from aioharmony.harmonyapi import HarmonyAPI, SendCommandDevice

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 15   # seconds
_IDLE_TIMEOUT = 300     # close connection after 5 min idle
_CMD_TIMEOUT = 10       # per-command timeout


# ---------------------------------------------------------------------------
# Persistent connection pool (one background loop, one connection per hub)
# ---------------------------------------------------------------------------

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None
_lock = threading.Lock()

# Keyed by hub_ip → {"api": HarmonyAPI, "last_used": float}
_pool: dict[str, dict] = {}
_pool_lock = asyncio.Lock()  # will be re-created on correct loop


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Start the background event loop thread (once)."""
    global _loop, _loop_thread, _pool_lock
    with _lock:
        if _loop is not None and _loop.is_running():
            return _loop
        loop = asyncio.new_event_loop()
        _pool_lock = asyncio.Lock()

        def _run():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        t = threading.Thread(target=_run, daemon=True, name="harmony-loop")
        t.start()
        _loop = loop
        _loop_thread = t
        # Start the idle reaper
        asyncio.run_coroutine_threadsafe(_idle_reaper(), loop)
        return loop


async def _get_api(hub_ip: str) -> HarmonyAPI:
    """Return a connected HarmonyAPI for *hub_ip*, reusing if possible."""
    async with _pool_lock:
        entry = _pool.get(hub_ip)
        if entry is not None:
            api = entry["api"]
            # Check if still connected
            if api.fw_version is not None:
                entry["last_used"] = time.monotonic()
                return api
            # Stale — clean up
            try:
                await api.close()
            except Exception:
                pass
            del _pool[hub_ip]

        # New connection
        api = HarmonyAPI(ip_address=hub_ip)
        connected = await asyncio.wait_for(api.connect(), timeout=_CONNECT_TIMEOUT)
        if not connected:
            raise ConnectionError(f"Could not connect to Harmony Hub at {hub_ip}")
        _pool[hub_ip] = {"api": api, "last_used": time.monotonic()}
        logger.info("Harmony Hub %s: connected (pool size=%d)", hub_ip, len(_pool))
        return api


async def _close_hub(hub_ip: str) -> None:
    """Close and remove a hub connection from the pool."""
    async with _pool_lock:
        entry = _pool.pop(hub_ip, None)
        if entry:
            try:
                await entry["api"].close()
            except Exception:
                pass
            logger.info("Harmony Hub %s: connection closed", hub_ip)


async def _idle_reaper():
    """Periodically close idle connections."""
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        to_close = []
        async with _pool_lock:
            for ip, entry in list(_pool.items()):
                if now - entry["last_used"] > _IDLE_TIMEOUT:
                    to_close.append(ip)
        for ip in to_close:
            await _close_hub(ip)


# ---------------------------------------------------------------------------
# Run-on-loop helper
# ---------------------------------------------------------------------------

def _run_on_hub(hub_ip: str, action):
    """Submit an async action to the persistent loop and block for result.

    *action* is an async callable that takes (api: HarmonyAPI) and returns a value.
    """
    loop = _ensure_loop()

    async def _wrapper():
        api = await _get_api(hub_ip)
        return await asyncio.wait_for(action(api), timeout=_CMD_TIMEOUT)

    future = asyncio.run_coroutine_threadsafe(_wrapper(), loop)
    try:
        return future.result(timeout=_CONNECT_TIMEOUT + _CMD_TIMEOUT)
    except Exception:
        # Connection may be bad — evict from pool so next call reconnects
        asyncio.run_coroutine_threadsafe(_close_hub(hub_ip), loop)
        raise


# ---------------------------------------------------------------------------
# One-shot helper (for test/config — no pool, clean connect+disconnect)
# ---------------------------------------------------------------------------

def _run_oneshot(hub_ip: str, action):
    """Connect, run action, disconnect. Used for test/config calls."""
    loop = _ensure_loop()

    async def _wrapper():
        api = HarmonyAPI(ip_address=hub_ip)
        try:
            connected = await asyncio.wait_for(api.connect(), timeout=_CONNECT_TIMEOUT)
            if not connected:
                raise ConnectionError(f"Could not connect to Harmony Hub at {hub_ip}")
            return await asyncio.wait_for(action(api), timeout=_CMD_TIMEOUT)
        finally:
            try:
                await api.close()
            except Exception:
                pass

    future = asyncio.run_coroutine_threadsafe(_wrapper(), loop)
    return future.result(timeout=_CONNECT_TIMEOUT + _CMD_TIMEOUT)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_current_activity(hub_ip: str) -> str:
    """Return the name of the currently running activity, or 'off'."""
    async def _action(api: HarmonyAPI):
        current = api.current_activity
        if current is None:
            return "off"
        if isinstance(current, tuple):
            act_id, act_name = current
            if act_id == -1:
                return "off"
            return act_name or f"Unknown ({act_id})"
        return str(current)

    try:
        return _run_on_hub(hub_ip, _action)
    except Exception as e:
        logger.error("get_current_activity(%s) failed: %s", hub_ip, e)
        return "unknown"


def start_activity(hub_ip: str, activity_name: str) -> bool:
    """Switch to the named activity.  Returns True on success."""
    async def _action(api: HarmonyAPI):
        config = api.config
        target_id = None
        for act in (config.get("activity") or []):
            if (act.get("label") or "").lower() == activity_name.lower():
                target_id = int(act["id"])
                break
        if target_id is None:
            raise ValueError(f"Activity '{activity_name}' not found on hub {hub_ip}")
        await api.start_activity(target_id)
        return True

    try:
        return _run_on_hub(hub_ip, _action)
    except Exception as e:
        logger.error("start_activity(%s, %s) failed: %s", hub_ip, activity_name, e)
        return False


def send_command(hub_ip: str, device_name: str, command: str,
                 fire_and_forget: bool = True) -> bool:
    """Send a single IR/BT command (e.g. VolumeUp, Mute, Back) to a device.

    With *fire_and_forget=True* (default), the command is dispatched to the
    Hub and control returns immediately (~50ms) without waiting for the
    library's 1-second error-check window.  Set to False if you need to
    know whether the Hub reported an error.
    """
    async def _action(api: HarmonyAPI):
        device_id = api.get_device_id(device_name)
        if device_id is None:
            raise ValueError(f"Device '{device_name}' not found on hub {hub_ip}")
        scd = SendCommandDevice(device=str(device_id), command=command, delay=0)
        if fire_and_forget:
            # Dispatch the command but don't wait for the 1s error window
            asyncio.ensure_future(api.send_commands(scd))
            # Small sleep to let the press+release actually transmit
            await asyncio.sleep(0.05)
        else:
            await api.send_commands(scd)
        return True

    try:
        return _run_on_hub(hub_ip, _action)
    except Exception as e:
        logger.error("send_command(%s, %s, %s) failed: %s", hub_ip, device_name, command, e)
        return False


def power_off(hub_ip: str) -> bool:
    """Turn off all devices (PowerOff activity = id -1)."""
    async def _action(api: HarmonyAPI):
        await api.power_off()
        return True

    try:
        return _run_on_hub(hub_ip, _action)
    except Exception as e:
        logger.error("power_off(%s) failed: %s", hub_ip, e)
        return False


def get_config(hub_ip: str) -> Optional[dict]:
    """Fetch the hub config (activities, devices).  Returns None on failure."""
    async def _action(api: HarmonyAPI):
        config = api.config
        activities = [
            {"id": a.get("id"), "label": a.get("label")}
            for a in (config.get("activity") or [])
        ]
        devices = [
            {"id": d.get("id"), "label": d.get("label")}
            for d in (config.get("device") or [])
        ]
        return {"activities": activities, "devices": devices}

    try:
        return _run_oneshot(hub_ip, _action)
    except Exception as e:
        logger.error("get_config(%s) failed: %s", hub_ip, e)
        return None
