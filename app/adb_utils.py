"""
adb_utils.py — Thin wrapper around the `adb` CLI for room/device management.

Provides structured diagnostics with TCP probing, raw ADB output, and
differentiated hint codes so the UI can display exactly why a connection failed.

Hint codes:
  adb_missing     — adb binary not found in container
  timeout         — TCP or ADB timed out (device may be off or blocked)
  unreachable     — no route to host / network unreachable
  refused         — TCP connection refused (port not listening)
  wrong_port      — TCP reachable but ADB handshake failed (wrong port)
  unauthorized    — ADB connected but device not authorized
  needs_pairing   — Chromecast needs `adb pair` before connect
  pair_failed     — pairing attempt failed (wrong code or port)
  unknown         — unclassified failure
"""

import logging
import os
import shutil
import socket
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

ADB_TIMEOUT = 10  # seconds for connect/pair
TCP_TIMEOUT = 1.5  # seconds for TCP probe
ADB_ENV = {**os.environ, "HOME": "/app/home"}

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _has_adb() -> bool:
    """Return True if the `adb` binary is on PATH."""
    return shutil.which("adb") is not None


def run_adb(args: list[str], timeout: int = ADB_TIMEOUT,
            input_text: Optional[str] = None) -> dict:
    """
    Run an ADB command and return structured result with raw output.

    Returns: {"ok": bool, "stdout": str, "stderr": str, "returncode": int}
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=ADB_ENV,
            input=input_text,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "returncode": -1,
        }
    except Exception as e:
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
        }


def tcp_probe(host: str, port: int, timeout: float = TCP_TIMEOUT) -> dict:
    """
    Attempt a raw TCP connection to host:port.

    Returns: {"ok": bool, "detail": str, "error_type": str|None}
    error_type is one of: None (success), "timeout", "refused", "unreachable"
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return {"ok": True, "detail": f"TCP port {port} is open", "error_type": None}
    except socket.timeout:
        return {"ok": False, "detail": f"TCP connection timed out after {timeout}s", "error_type": "timeout"}
    except ConnectionRefusedError:
        return {"ok": False, "detail": f"TCP connection refused on port {port}", "error_type": "refused"}
    except OSError as e:
        err_str = str(e).lower()
        if "no route to host" in err_str or "network is unreachable" in err_str:
            return {"ok": False, "detail": str(e), "error_type": "unreachable"}
        return {"ok": False, "detail": str(e), "error_type": "unreachable"}
    except Exception as e:
        return {"ok": False, "detail": str(e), "error_type": "unreachable"}


def resolve_host(host: str) -> dict:
    """
    Resolve hostname to an IP address.

    Returns: {"ok": bool, "detail": str, "ip": str|None}
    """
    try:
        ip = socket.gethostbyname(host)
        return {"ok": True, "detail": f"Resolved to {ip}", "ip": ip}
    except socket.gaierror as e:
        return {"ok": False, "detail": f"DNS resolution failed: {e}", "ip": None}


def _classify_adb_output(stdout: str, stderr: str) -> dict:
    """
    Classify ADB output into hint code + human-readable detail.

    Returns: {"ok": bool, "hint": str, "detail": str, "needsPairing": bool}
    """
    combined = (stdout + " " + stderr).lower()

    if "already connected" in combined or "connected to" in combined:
        return {"ok": True, "hint": "connected", "detail": stdout.strip(), "needsPairing": False}

    if "unauthorized" in combined:
        return {"ok": False, "hint": "unauthorized", "needsPairing": True,
                "detail": "Device unauthorized — accept the ADB prompt on the TV, or pair with a code."}

    if "failed to authenticate" in combined or "pairing failed" in combined:
        return {"ok": False, "hint": "needs_pairing", "needsPairing": True,
                "detail": "Authentication failed — device needs pairing."}

    if "connection refused" in combined:
        return {"ok": False, "hint": "refused", "needsPairing": False,
                "detail": "ADB connection refused on this port."}

    if "no route to host" in combined or "network is unreachable" in combined:
        return {"ok": False, "hint": "unreachable", "needsPairing": False,
                "detail": "Network unreachable — device may be off or on a different network."}

    if "cannot connect" in combined or "failed to connect" in combined:
        return {"ok": False, "hint": "wrong_port", "needsPairing": False,
                "detail": "ADB could not connect — port may be wrong or not an ADB port."}

    # Generic fallback
    detail = stdout.strip() or stderr.strip() or "Unknown ADB error"
    return {"ok": False, "hint": "unknown", "needsPairing": False, "detail": detail}


# ---------------------------------------------------------------------------
# Key-event map for remote control commands
# ---------------------------------------------------------------------------
KEYEVENT_MAP = {
    'power': 26,
    'play': 126,
    'pause': 127,
    'play_pause': 85,
    'stop': 86,
    'vol_up': 24,
    'vol_down': 25,
    'mute': 164,
    'home': 3,
    'back': 4,
    'up': 19,
    'down': 20,
    'left': 21,
    'right': 22,
    'select': 23,
    'channel_up': 166,
    'channel_down': 167,
}


def get_wake_state(host: str, port: int, timeout: int = 5) -> str:
    """Query display wakefulness via ADB.

    Returns one of: ``'Awake'``, ``'Asleep'``, ``'Doze'``, or ``'unknown'``.
    The caller can pass a shorter *timeout* during polling loops where
    each failed probe against an offline device burns the full window.
    """
    target = f"{host}:{port}"
    result = run_adb(
        ["adb", "-s", target, "shell", "dumpsys", "power"],
        timeout=timeout,
    )
    if not result["ok"]:
        return "unknown"
    for line in result["stdout"].splitlines():
        line = line.strip()
        if line.startswith("mWakefulness="):
            return line.split("=", 1)[1]
    return "unknown"


def send_key_event(host: str, port: int, key_name: str, mac_address: str = '') -> dict:
    """Send an ADB keyevent to ``host:port``.

    If *mac_address* is provided and valid, a Wake-on-LAN magic packet is sent
    first.  For ``power`` commands a 3-second delay follows so the device can
    boot its network stack before ADB connect is attempted.

    Returns: {"ok": bool, "detail": str}
    """
    import time

    # Wake-on-LAN: send magic packet before ADB connect
    if mac_address:
        from app.wol import is_valid_mac, send_wol
        if is_valid_mac(mac_address):
            wol_result = send_wol(mac_address)
            logger.info("WoL for %s:%s → %s", host, port, wol_result.get('detail'))
            if key_name == 'power':
                time.sleep(1.5)

    if not _has_adb():
        return {"ok": False, "detail": "ADB binary not found in container."}

    code = KEYEVENT_MAP.get(key_name)
    if code is None:
        return {"ok": False, "detail": f"Unknown command: {key_name}"}

    target = f"{host}:{port}"
    # Ensure ADB connection is active (stale connections silently drop)
    run_adb(["adb", "connect", target], timeout=3)
    result = run_adb(["adb", "-s", target, "shell", "input", "keyevent", str(code)])
    detail = result["stdout"].strip() or result["stderr"].strip() or "OK"
    return {"ok": result["ok"], "detail": detail}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def adb_connect(host: str, connect_port: int) -> dict:
    """
    Run `adb connect host:port`.
    Disconnects first to avoid stale state.

    Returns: {"ok": bool, "needsPairing": bool, "hint": str, "detail": str,
              "stdout": str, "stderr": str}
    """
    if not _has_adb():
        return {"ok": False, "needsPairing": False, "hint": "adb_missing",
                "detail": "ADB binary not found in container.", "stdout": "", "stderr": ""}

    target = f"{host}:{connect_port}"

    # Disconnect stale sessions (ignore errors)
    run_adb(["adb", "disconnect", target], timeout=5)

    result = run_adb(["adb", "connect", target])

    if result["returncode"] == -1 and "timed out" in result["stderr"]:
        return {"ok": False, "needsPairing": False, "hint": "timeout",
                "detail": f"ADB connect timed out after {ADB_TIMEOUT}s.",
                "stdout": result["stdout"], "stderr": result["stderr"]}

    classification = _classify_adb_output(result["stdout"], result["stderr"])
    logger.info("adb connect %s → ok=%s hint=%s", target, classification["ok"], classification["hint"])

    return {
        **classification,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def adb_pair(host: str, pair_port: int, pair_code: str) -> dict:
    """
    Run `adb pair host:pairPort pairCode`.

    Returns: {"ok": bool, "needsPairing": bool, "hint": str, "detail": str,
              "stdout": str, "stderr": str}
    """
    if not _has_adb():
        return {"ok": False, "needsPairing": False, "hint": "adb_missing",
                "detail": "ADB binary not found in container.", "stdout": "", "stderr": ""}

    target = f"{host}:{pair_port}"
    result = run_adb(["adb", "pair", target, pair_code])

    combined = (result["stdout"] + " " + result["stderr"]).lower()

    if "successfully paired" in combined:
        logger.info("adb pair %s succeeded", target)
        return {"ok": True, "needsPairing": False, "hint": "paired",
                "detail": "Pairing succeeded.",
                "stdout": result["stdout"], "stderr": result["stderr"]}

    if result["returncode"] == -1 and "timed out" in result["stderr"]:
        return {"ok": False, "needsPairing": True, "hint": "timeout",
                "detail": f"Pairing timed out after {ADB_TIMEOUT}s.",
                "stdout": result["stdout"], "stderr": result["stderr"]}

    classification = _classify_adb_output(result["stdout"], result["stderr"])
    # Override hint for pairing-specific failures
    if classification["hint"] in ("wrong_port", "refused", "unknown"):
        classification["hint"] = "pair_failed"
        classification["detail"] = f"Pairing failed — check the pair port and code. Raw: {result['stdout'].strip() or result['stderr'].strip()}"

    logger.warning("adb pair %s → hint=%s", target, classification["hint"])
    return {
        **classification,
        "needsPairing": True,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def adb_devices() -> list[dict]:
    """
    Run `adb devices` and return a list of {serial, state} dicts.
    State is typically "device", "unauthorized", or "offline".
    """
    if not _has_adb():
        return []

    result = run_adb(["adb", "devices"], timeout=5)
    devices = []
    for line in result["stdout"].strip().splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1]})
    return devices


def test_room_connection(host: str, connect_port: int, platform: str = "android_tv") -> dict:
    """
    Full structured test of a room's ADB connectivity.

    Steps:
      1. resolve_host  — DNS resolution
      2. tcp_probe     — raw TCP socket open
      3. adb_connect   — `adb connect host:port`
      4. adb_devices   — verify device state in `adb devices`
      5. shell_test    — `adb shell getprop ro.product.model`

    Returns:
    {
        "ok": bool,
        "needsPairing": bool,
        "hint": str,          # classified failure hint
        "message": str,
        "steps": [{"step": str, "ok": bool, "detail": str, "stdout": str?, "stderr": str?}, ...]
    }
    """
    steps = []
    hint = None

    # --- Step 1: Resolve host ---
    dns = resolve_host(host)
    steps.append({"step": "resolve_host", "ok": dns["ok"], "detail": dns["detail"]})
    if not dns["ok"]:
        return {
            "ok": False, "needsPairing": False, "hint": "unreachable",
            "message": f"Cannot resolve hostname '{host}'.",
            "steps": steps,
        }

    # --- Step 2: TCP probe ---
    tcp = tcp_probe(host, connect_port)
    steps.append({"step": "tcp_probe", "ok": tcp["ok"], "detail": tcp["detail"]})
    if not tcp["ok"]:
        hint = tcp["error_type"] or "unreachable"
        if hint == "refused":
            msg = f"Port {connect_port} is not listening. "
            if platform == "chromecast_gtv":
                msg += "On Chromecast, go to Settings > Developer Options > Wireless Debugging and check the port shown under 'IP address & Port'."
            else:
                msg += "Ensure ADB debugging is enabled on the device."
        elif hint == "timeout":
            msg = f"TCP connection to {host}:{connect_port} timed out. Device may be off, asleep, or firewall is blocking."
        else:
            msg = f"Cannot reach {host}:{connect_port} — device may be off or on a different network."
        return {
            "ok": False, "needsPairing": False, "hint": hint,
            "message": msg, "steps": steps,
        }

    # --- Step 3: ADB connect ---
    if not _has_adb():
        steps.append({"step": "adb_connect", "ok": False, "detail": "ADB binary not found in container."})
        return {
            "ok": False, "needsPairing": False, "hint": "adb_missing",
            "message": "ADB binary not found in the Docker container.",
            "steps": steps,
        }

    conn = adb_connect(host, connect_port)
    steps.append({
        "step": "adb_connect",
        "ok": conn["ok"],
        "detail": conn["detail"],
        "stdout": conn.get("stdout", ""),
        "stderr": conn.get("stderr", ""),
    })

    if not conn["ok"]:
        # TCP was OK but ADB failed — likely wrong port or needs pairing
        adb_hint = conn.get("hint", "unknown")
        if adb_hint in ("wrong_port", "refused", "unknown") and tcp["ok"]:
            hint = "wrong_port"
            msg = f"TCP port {connect_port} is open but ADB handshake failed. "
            if platform == "chromecast_gtv":
                msg += "This is probably not the Wireless Debugging port. Check Settings > Developer Options > Wireless Debugging for the correct port."
            else:
                msg += "This port is not running ADB. Shield/Android TV typically uses port 5555."
        elif adb_hint in ("unauthorized", "needs_pairing"):
            hint = adb_hint
            msg = conn["detail"]
        elif adb_hint == "timeout":
            hint = "timeout"
            msg = f"ADB connect timed out. Device may be slow to respond or port {connect_port} is not ADB."
        else:
            hint = adb_hint
            msg = conn["detail"]

        return {
            "ok": False,
            "needsPairing": conn.get("needsPairing", False),
            "hint": hint,
            "message": msg,
            "steps": steps,
        }

    # --- Step 4: adb devices ---
    devices = adb_devices()
    target = f"{host}:{connect_port}"
    device_entry = next((d for d in devices if d["serial"] == target), None)

    if device_entry:
        state = device_entry["state"]
        dev_ok = state == "device"
        steps.append({
            "step": "adb_devices",
            "ok": dev_ok,
            "detail": f"State: {state}",
        })
        if state == "unauthorized":
            return {
                "ok": False, "needsPairing": True, "hint": "unauthorized",
                "message": "Device is unauthorized. Accept the ADB debugging prompt on the TV, or pair with a code.",
                "steps": steps,
            }
        if not dev_ok:
            return {
                "ok": False, "needsPairing": False, "hint": "unknown",
                "message": f"Device state is '{state}' — expected 'device'.",
                "steps": steps,
            }
    else:
        steps.append({
            "step": "adb_devices",
            "ok": False,
            "detail": f"{target} not found in adb devices list",
        })
        return {
            "ok": False, "needsPairing": False, "hint": "unknown",
            "message": "ADB connected but device not found in device list.",
            "steps": steps,
        }

    # --- Step 5: Shell test ---
    shell_result = run_adb(
        ["adb", "-s", target, "shell", "getprop", "ro.product.model"],
        timeout=5,
    )
    model = shell_result["stdout"].strip()
    steps.append({
        "step": "shell_test",
        "ok": bool(model),
        "detail": f"Device model: {model}" if model else "No response from shell",
        "stdout": shell_result["stdout"],
        "stderr": shell_result["stderr"],
    })

    all_ok = all(s["ok"] for s in steps)
    return {
        "ok": all_ok,
        "needsPairing": False,
        "hint": "connected" if all_ok else "unknown",
        "message": f"Connected! Device model: {model}" if all_ok else "Partial connection — see steps for details.",
        "steps": steps,
    }
