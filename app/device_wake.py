"""
device_wake.py — Device wake orchestration for playback.

Inspects the device/room config and runs the appropriate wake sequence
(WoL, ADB connect, Harmony switch, HA power, display poll) so the
caller can assume the device is ready for playback.

Each step is best-effort: failures are logged but never abort the
sequence, because a partially-awake device is still worth attempting
playback on.
"""

from app.err_text import public as _err_public
import logging
import time

from app.adb_utils import run_adb, get_wake_state
from app.wol import is_valid_mac, send_wol

logger = logging.getLogger(__name__)

# Platforms that use ADB for wake/display management
_ADB_PLATFORMS = {'android_tv', 'chromecast_gtv', ''}  # '' = legacy/unset


def ensure_device_ready(device: dict, room: dict,
                        timeout: int = 10) -> dict:
    """Orchestrate the full wake sequence for *device* in *room*.

    Returns ``{"ok": bool, "detail": str, "steps": [str, ...]}``.
    """
    steps: list[str] = []
    host = device.get('host', '')
    port = int(device.get('connectPort', 5555) or 5555)
    platform = device.get('platform', '')

    if not host:
        return {"ok": False, "detail": "No device host configured", "steps": steps}

    # ── Network-only devices (no ADB) ──
    if platform == 'network':
        mac = device.get('macAddress', '')
        if mac and is_valid_mac(mac):
            _step_wol(mac, steps)
        _step_harmony_switch(room, device, steps)
        return {"ok": True, "detail": "Network device — WoL/Harmony only", "steps": steps}

    # ── Home Assistant power path ──
    # When powerMethod is 'homeassistant', skip the slow ADB connect attempts
    # to a powered-off TV. Fire HA first, then wait for the device to boot.
    if device.get('powerMethod') == 'homeassistant':
        return _ha_wake_sequence(device, room, host, port, platform, timeout, steps)

    # ── Standard ADB-based wake path ──
    return _adb_wake_sequence(device, room, host, port, platform, timeout, steps)


def _ha_wake_sequence(device: dict, room: dict,
                      host: str, port: int, platform: str,
                      timeout: int, steps: list[str]) -> dict:
    """Wake sequence optimized for HA-powered devices.

    HA entities typically power the TV on nearly instantly, so the real
    wait is for Android to boot and ADB to come online (~10-15s).
    We minimize overhead by firing HA as early as possible and polling
    with built-in ADB reconnect attempts.
    """
    # 1. Quick ADB probe (1s) — just checking if already on
    _step_adb_connect(host, port, steps, connect_timeout=1)
    state = _step_check_display(host, port, steps)

    if state == 'Awake':
        return {"ok": True, "detail": "Device already awake", "steps": steps}

    # 2. Fire HA entity immediately — this is the fast part
    ha_fired = _step_ha_power(device, state, steps)

    # 3. WoL — helps ADB come online faster once Android boots
    mac = device.get('macAddress', '')
    if mac and is_valid_mac(mac):
        _step_wol(mac, steps)

    if ha_fired:
        # 4. Poll immediately with ADB reconnect — no boot wait needed,
        #    HA powers the TV on fast, we just wait for Android/ADB.
        #    GTV cold-boot from standby is typically 30-50s. 60s gives us
        #    headroom without blocking the request excessively.
        ha_timeout = max(timeout, 60)
        awake = _step_poll_awake(host, port, ha_timeout, steps, reconnect=True)
        if awake:
            return {"ok": True, "detail": "Device awake (HA power)", "steps": steps}

        return {
            "ok": False,
            "detail": f"HA fired but device not awake after {ha_timeout}s",
            "steps": steps,
        }

    # HA didn't fire (not configured, etc.) — fall back to standard ADB path
    steps.append("ha_fallback: falling back to ADB wake")
    return _adb_wake_sequence(device, room, host, port, platform, timeout, steps)


def _adb_wake_sequence(device: dict, room: dict,
                       host: str, port: int, platform: str,
                       timeout: int, steps: list[str]) -> dict:
    """Standard ADB-based wake sequence (WoL, Harmony, ADB keyevents)."""

    # 1. Wake-on-LAN (skip when Harmony handles power — it controls the whole chain)
    has_harmony = room.get('useHarmonyHub') and device.get('harmonyActivity')
    mac = device.get('macAddress', '')
    if mac and is_valid_mac(mac) and not has_harmony:
        _step_wol(mac, steps)

    # 2. Initial ADB connect
    _step_adb_connect(host, port, steps)

    # 3. Check display state
    state = _step_check_display(host, port, steps)

    if state == 'Awake':
        return {"ok": True, "detail": "Device already awake", "steps": steps}

    # 4. Harmony switch (if configured) — may confirm awake via polling
    harmony_confirmed = _step_harmony_switch(room, device, steps)

    if harmony_confirmed:
        return {"ok": True, "detail": "Device awake (confirmed by Harmony poll)", "steps": steps}

    # 5. Reconnect ADB (Harmony may have power-cycled the HDMI-CEC chain)
    _step_adb_connect(host, port, steps)

    # 6. ADB wakeup keyevent
    _step_adb_wake(host, port, platform, state, steps)

    # 7. Poll until awake
    awake = _step_poll_awake(host, port, timeout, steps)

    return {
        "ok": awake,
        "detail": "Device awake" if awake else f"Device not confirmed awake after {timeout}s",
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Private step helpers
# ---------------------------------------------------------------------------

def _step_wol(mac: str, steps: list[str]) -> None:
    try:
        result = send_wol(mac)
        steps.append(f"wol: {result.get('detail', 'sent')}")
        time.sleep(0.5)
    except Exception as e:
        logger.warning("WoL failed for %s: %s", mac, e)
        steps.append(f"wol: failed — {_err_public(e, 'error', 'device_wake')}")


def _step_adb_connect(host: str, port: int, steps: list[str],
                      connect_timeout: int = 5) -> None:
    try:
        target = f"{host}:{port}"
        result = run_adb(["adb", "connect", target], timeout=connect_timeout)
        detail = result["stdout"].strip() or result["stderr"].strip() or "OK"
        steps.append(f"adb_connect: {detail}")
    except Exception as e:
        logger.warning("ADB connect failed for %s:%s: %s", host, port, e)
        steps.append(f"adb_connect: failed — {_err_public(e, 'error', 'device_wake')}")


def _step_check_display(host: str, port: int, steps: list[str]) -> str:
    try:
        state = get_wake_state(host, port)
        steps.append(f"display_check: {state}")
        return state
    except Exception as e:
        logger.warning("Display check failed for %s:%s: %s", host, port, e)
        steps.append(f"display_check: failed — {_err_public(e, 'error', 'device_wake')}")
        return "unknown"


def _step_harmony_switch(room: dict, device: dict,
                         steps: list[str]) -> bool:
    """Switch Harmony activity via direct Hub connection. Returns True if device confirmed awake."""
    if not (room.get('useHarmonyHub') and device.get('harmonyActivity')):
        return False
    hub_ip = room.get('harmonyHubIp', '')
    activity = device.get('harmonyActivity', '')
    if not (hub_ip and activity):
        return False

    from app.harmony_service import get_current_activity, start_activity

    try:
        current = get_current_activity(hub_ip)
        steps.append(f"harmony_state: current='{current}', target='{activity}'")

        if current.lower() == activity.lower():
            steps.append("harmony_switch: already correct")
            return False

        ok = start_activity(hub_ip, activity)
        if not ok:
            steps.append("harmony_switch: start_activity returned False")
            return False

        if current in ('off', 'unknown'):
            # Brief poll — if not awake yet, ADB wake step handles the rest
            steps.append("harmony_switch: power on — polling for wake")
            for elapsed in range(1, 6):  # max 5s
                time.sleep(1)
                state = get_wake_state(
                    device.get('host', ''),
                    int(device.get('connectPort', 5555) or 5555),
                )
                if state == 'Awake':
                    steps.append(f"harmony_switch: device awake after {elapsed}s")
                    return True  # confirmed awake — caller can skip remaining steps
            steps.append("harmony_switch: not confirmed awake after 5s, continuing")
        else:
            steps.append(f"harmony_switch: '{current}' → '{activity}' — waiting 3s")
            time.sleep(3)
    except Exception as e:
        logger.warning("Harmony switch failed: %s", e)
        steps.append(f"harmony_switch: failed — {_err_public(e, 'error', 'device_wake')}")
    return False


def _step_ha_power(device: dict, display_state: str,
                   steps: list[str]) -> bool:
    """Fire the HA power entity when display is not confirmed awake.

    Fires when *display_state* is ``Asleep``, ``Doze``, or ``unknown``
    (TV fully off means ADB can't query state at all → ``unknown``).
    Skips only when ``Awake`` — but the caller already returns early
    for ``Awake``, so this is just a safety guard.

    Returns True if the entity was fired, False if skipped.
    """
    entity_id = device.get('haPowerEntity', '')
    if not entity_id:
        steps.append("ha_power: skipped — no entity configured")
        return False

    if display_state == 'Awake':
        steps.append("ha_power: skipped — display already Awake")
        return False

    from app.settings_manager import SettingsManager
    _settings = SettingsManager()
    ha_url = _settings.get('homeassistant.url', '')
    ha_token = _settings.get('homeassistant.access_token', '')
    if not ha_url or not ha_token:
        steps.append("ha_power: skipped — HA not configured in settings")
        return False

    from app.ha_service import fire_entity
    result = fire_entity(ha_url, ha_token, entity_id)
    steps.append(f"ha_power: {result.get('detail', 'unknown')}")
    return result.get('ok', False)


def _step_adb_wake(host: str, port: int, platform: str,
                   display_state: str, steps: list[str]) -> None:
    """Send wake keyevent(s) via ADB.

    KEYCODE_WAKEUP (224) is safe (non-toggle, no-op when already awake)
    but Chromecast/GTV devices often ignore it.

    KEYCODE_POWER (26) is a toggle — it works on GTV but would turn an
    awake device *off*.  We only send it when the display state is
    confirmed asleep/dozing, so there's no risk of toggling off.
    """
    target = f"{host}:{port}"

    # KEYCODE_POWER (26) — only when we're sure the display is off
    confirmed_off = display_state in ('Asleep', 'Doze')
    if confirmed_off:
        try:
            result = run_adb(["adb", "-s", target, "shell",
                              "input", "keyevent", "26"], timeout=5)
            steps.append(f"adb_wake_power: {'OK' if result['ok'] else result['stderr'].strip()}")
        except Exception as e:
            logger.warning("ADB POWER failed for %s:%s: %s", host, port, e)
            steps.append(f"adb_wake_power: failed — {_err_public(e, 'error', 'device_wake')}")

    # KEYCODE_WAKEUP (224) — always safe to send
    try:
        result = run_adb(["adb", "-s", target, "shell",
                          "input", "keyevent", "KEYCODE_WAKEUP"], timeout=5)
        steps.append(f"adb_wake_wakeup: {'OK' if result['ok'] else result['stderr'].strip()}")
    except Exception as e:
        logger.warning("ADB WAKEUP failed for %s:%s: %s", host, port, e)
        steps.append(f"adb_wake_wakeup: failed — {_err_public(e, 'error', 'device_wake')}")


def _step_poll_awake(host: str, port: int, timeout: int,
                     steps: list[str], reconnect: bool = False) -> bool:
    """Poll dumpsys power until the display is Awake or we hit *timeout* seconds.

    When *reconnect* is True we also try to re-establish the ADB connection
    periodically — the device may have booted into a fresh ADB listener.

    get_wake_state is called with a short per-probe timeout (2s) so an
    offline device doesn't burn the entire window on one stuck probe.
    """
    target = f"{host}:{port}"
    start = time.monotonic()
    iteration = 0
    while time.monotonic() - start < timeout:
        iteration += 1
        # Retry ADB connect every other iteration (~every 2s) once reconnect is on
        if reconnect and iteration % 2 == 0:
            try:
                run_adb(["adb", "connect", target], timeout=2)
            except Exception:
                pass
        state = get_wake_state(host, port, timeout=2)
        if state == 'Awake':
            elapsed = int(time.monotonic() - start)
            steps.append(f"poll_awake: Awake after {elapsed}s ({iteration} probes)")
            return True
        time.sleep(1)
    steps.append(f"poll_awake: not awake after {timeout}s ({iteration} probes)")
    return False
