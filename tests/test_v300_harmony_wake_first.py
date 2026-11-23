"""v300: Harmony runs before the "player already awake" shortcut, and the
player's screen is read again after a switch so KEYCODE_POWER (a toggle) is
only sent when it is really still off. No hub, TV or ADB is touched."""
import sys
import types

import pytest

from app import device_wake as dw

ROOM = {"useHarmonyHub": True, "harmonyHubIp": "192.0.2.19"}
DEV = {"host": "192.0.2.126", "connectPort": 5555, "platform": "android_tv",
       "harmonyActivity": "SHIELD TV"}


@pytest.fixture
def rig(monkeypatch):
    calls = {"adb": [], "start": [], "states": []}
    hub = {"current": "off", "start_ok": True}
    states = []

    def fake_state(host, port, timeout=None):
        calls["states"].append(1)
        return states.pop(0) if states else "Awake"

    def fake_run_adb(args, timeout=None, input_text=None):
        calls["adb"].append(args)
        return {"ok": True, "stdout": "connected", "stderr": "", "returncode": 0}

    hs = types.ModuleType("app.harmony_service")
    hs.get_current_activity = lambda ip: hub["current"]

    def start(ip, act):
        calls["start"].append(act)
        hub["current"] = act
        return hub["start_ok"]
    hs.start_activity = start
    monkeypatch.setitem(sys.modules, "app.harmony_service", hs)
    monkeypatch.setattr(dw, "get_wake_state", fake_state)
    monkeypatch.setattr(dw, "run_adb", fake_run_adb)
    monkeypatch.setattr(dw.time, "sleep", lambda s: None)
    monkeypatch.setattr(dw, "send_wol", lambda mac: {"detail": "sent"})
    return calls, hub, states


def _keys(calls):
    return [a[-1] for a in calls["adb"] if "keyevent" in a]


def test_awake_player_with_tv_off_still_starts_harmony(rig):
    calls, hub, states = rig
    hub["current"] = "off"
    states[:] = ["Awake", "Awake"]
    r = dw.ensure_device_ready(dict(DEV), dict(ROOM))
    assert calls["start"] == ["SHIELD TV"]
    assert r["ok"] is True
    assert _keys(calls) == []  # no POWER toggle on an awake player


def test_poweroff_label_counts_as_off(rig):
    calls, hub, states = rig
    hub["current"] = "PowerOff"
    states[:] = ["Asleep", "Awake"]
    r = dw.ensure_device_ready(dict(DEV), dict(ROOM))
    assert calls["start"] == ["SHIELD TV"]
    assert r["ok"] is True and "harmony_switch: device awake after 1s" in r["steps"]
    assert _keys(calls) == []


def test_activity_already_running_and_awake_is_fast(rig):
    calls, hub, states = rig
    hub["current"] = "SHIELD TV"
    states[:] = ["Awake"]
    r = dw.ensure_device_ready(dict(DEV), dict(ROOM))
    assert calls["start"] == []
    assert r["ok"] is True and r["detail"] == "Device already awake"
    assert _keys(calls) == []


def test_power_toggle_uses_fresh_state_after_switch(rig):
    # Player read Asleep before Harmony; Harmony woke it after the 5s poll.
    calls, hub, states = rig
    hub["current"] = "off"
    states[:] = ["Asleep"] + ["Asleep"] * 5 + ["Awake"]
    r = dw.ensure_device_ready(dict(DEV), dict(ROOM))
    assert calls["start"] == ["SHIELD TV"]
    assert r["ok"] is True
    assert "26" not in _keys(calls)


def test_power_toggle_sent_when_still_off_after_switch(rig):
    calls, hub, states = rig
    hub["current"] = "Watch Ugoos"
    states[:] = ["Asleep", "Asleep", "Awake"]
    r = dw.ensure_device_ready(dict(DEV), dict(ROOM))
    assert calls["start"] == ["SHIELD TV"]
    assert _keys(calls) == ["26", "KEYCODE_WAKEUP"]
    assert r["ok"] is True


def test_already_correct_but_asleep_wakes_via_adb(rig):
    calls, hub, states = rig
    hub["current"] = "SHIELD TV"
    states[:] = ["Asleep", "Awake"]
    r = dw.ensure_device_ready(dict(DEV), dict(ROOM))
    assert calls["start"] == []
    assert _keys(calls) == ["26", "KEYCODE_WAKEUP"]
    assert r["ok"] is True


def test_no_harmony_room_keeps_old_shortcut(rig):
    calls, hub, states = rig
    states[:] = ["Awake"]
    r = dw.ensure_device_ready(dict(DEV, harmonyActivity=""), {"useHarmonyHub": False})
    assert calls["start"] == []
    assert r["detail"] == "Device already awake"
