#!/usr/bin/env python3
"""
Cloudflare Tunnel subprocess manager.

Manages a cloudflared tunnel as a subprocess — start, stop, monitor,
auto-restart on crash, and auto-start at Flask boot if enabled in settings.
"""

from app.err_text import public as _err_public
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (singleton pattern, same as push_service.py)
# ---------------------------------------------------------------------------
_process: subprocess.Popen | None = None
_lock = threading.Lock()
_enabled = False
_started_at: datetime | None = None
_last_error: str = ""
_monitor_thread: threading.Thread | None = None


def start_tunnel(token: str) -> dict:
    """Spawn cloudflared tunnel subprocess.

    Returns a status dict with success/message.
    """
    global _process, _enabled, _started_at, _last_error, _monitor_thread

    if not token or not token.strip():
        return {"success": False, "message": "Tunnel token is required"}

    binary = shutil.which("cloudflared")
    if not binary:
        return {"success": False, "message": "cloudflared binary not found"}

    with _lock:
        # Already running?
        if _process and _process.poll() is None:
            return {"success": True, "message": "Tunnel is already running"}

        try:
            _process = subprocess.Popen(
                [binary, "tunnel", "--no-autoupdate", "run", "--token", token],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            _enabled = True
            _started_at = datetime.now(timezone.utc)
            _last_error = ""

            # Start monitor thread (auto-restart on crash)
            if _monitor_thread is None or not _monitor_thread.is_alive():
                _monitor_thread = threading.Thread(
                    target=_monitor_process,
                    args=(token,),
                    daemon=True,
                    name="cloudflared-monitor",
                )
                _monitor_thread.start()

            logger.info(f"Cloudflare Tunnel started (PID {_process.pid})")
            return {"success": True, "message": f"Tunnel started (PID {_process.pid})"}

        except Exception as e:
            _last_error = _err_public(e, "Tunnel error", "tunnel")
            logger.error(f"Failed to start cloudflared: {e}")
            return {"success": False, "message": _last_error}


def stop_tunnel() -> dict:
    """Terminate the cloudflared process gracefully."""
    global _process, _enabled, _started_at, _last_error

    with _lock:
        _enabled = False

        if _process is None or _process.poll() is not None:
            _process = None
            _started_at = None
            return {"success": True, "message": "Tunnel is not running"}

        pid = _process.pid
        try:
            _process.terminate()
            try:
                _process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _process.kill()
                _process.wait(timeout=5)
            logger.info(f"Cloudflare Tunnel stopped (PID {pid})")
        except Exception as e:
            _last_error = _err_public(e, "Tunnel error", "tunnel")
            logger.error(f"Error stopping cloudflared: {e}")
            return {"success": False, "message": _last_error}
        finally:
            _process = None
            _started_at = None

    return {"success": True, "message": "Tunnel stopped"}


def get_status() -> dict:
    """Return current tunnel status."""
    with _lock:
        running = _process is not None and _process.poll() is None
        pid = _process.pid if running and _process else None
        uptime = None
        if running and _started_at:
            uptime = (datetime.now(timezone.utc) - _started_at).total_seconds()

    return {
        "running": running,
        "enabled": _enabled,
        "pid": pid,
        "started_at": _started_at.isoformat() if _started_at else None,
        "uptime_seconds": round(uptime) if uptime else None,
        "last_error": _last_error,
    }


def _monitor_process(token: str) -> None:
    """Daemon thread: detect crashes and auto-restart while enabled."""
    global _process, _started_at, _last_error

    while True:
        time.sleep(5)

        with _lock:
            if not _enabled:
                return  # Tunnel was intentionally stopped

            if _process is None:
                return

            rc = _process.poll()
            if rc is None:
                continue  # Still running

            # Process exited — read stderr for error info
            stderr_output = ""
            try:
                stderr_output = _process.stderr.read() if _process.stderr else ""
            except Exception:
                pass

            _last_error = f"Process exited with code {rc}"
            if stderr_output:
                # Keep last 200 chars of stderr
                _last_error += f": {stderr_output[-200:]}"
            logger.warning(f"cloudflared exited (code {rc}), restarting...")

        # Restart outside the lock
        time.sleep(3)  # Brief backoff before restart

        with _lock:
            if not _enabled:
                return

        binary = shutil.which("cloudflared")
        if not binary:
            _last_error = "cloudflared binary not found on restart"
            logger.error(_last_error)
            return

        with _lock:
            if not _enabled:
                return
            try:
                _process = subprocess.Popen(
                    [binary, "tunnel", "--no-autoupdate", "run", "--token", token],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                _started_at = datetime.now(timezone.utc)
                _last_error = ""
                logger.info(f"cloudflared restarted (PID {_process.pid})")
            except Exception as e:
                _last_error = _err_public(e, "Tunnel error", "tunnel")
                logger.error(f"Failed to restart cloudflared: {e}")
                return


def auto_start_if_enabled(settings_manager) -> None:
    """Called at Flask boot — start tunnel if enabled in settings."""
    enabled = settings_manager.get("network.tunnel.enabled", False)
    token = settings_manager.get("network.tunnel.token", "")
    if enabled and token:
        logger.info("Auto-starting Cloudflare Tunnel from saved settings...")
        result = start_tunnel(token)
        if result["success"]:
            logger.info(f"Cloudflare Tunnel auto-started: {result['message']}")
        else:
            logger.error(f"Cloudflare Tunnel auto-start failed: {result['message']}")
    else:
        logger.debug("Cloudflare Tunnel not enabled in settings, skipping auto-start")


def get_cloudflared_version() -> str | None:
    """Return cloudflared version string, or None if binary not found."""
    binary = shutil.which("cloudflared")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "version"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or result.stderr.strip()
    except Exception:
        return None
