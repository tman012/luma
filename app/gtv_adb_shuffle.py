"""Native Plex Shuffle on Google TV via ADB UI automation.

GTV Plex is not in /clients and ignores remote playQueue shuffle mutations.
Manual Shuffle in the Plex TV UI creates a native local shuffled playQueue.
This module reproduces that path: open show/season → More → Shuffle.

HARD RULE: only talk to the provided device_ip:adb_port. Never other ADB devices.
"""

from __future__ import annotations

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import requests

from app.adb_utils import run_adb as _run_adb_raw

logger = logging.getLogger(__name__)

_ADB_CANDIDATES = [
    os.environ.get("ADB_PATH") or "",
    "/home/tbosley02/Android/Sdk/platform-tools/adb",
    "/sdk/platform-tools/adb",
    "/usr/bin/adb",
    "adb",
]


def _adb_bin() -> str:
    for c in _ADB_CANDIDATES:
        if not c:
            continue
        if c == "adb" or Path(c).exists():
            return c
    return "adb"


def run_adb(args: list, timeout: int = 15, input_text=None) -> dict:
    """Like adb_utils.run_adb but rewrites leading 'adb' to a resolved binary.

    adb_utils forces HOME=/app/home (container). On the host that path is missing,
    so we invoke adb ourselves with a writable HOME.
    """
    import subprocess
    args = list(args)
    if args and args[0] == "adb":
        args[0] = _adb_bin()

    home = "/app/home" if Path("/app/home").exists() else str(Path.home())
    env = {**os.environ, "HOME": home}
    # Keep container android keys when present
    if Path("/app/home/.android").exists():
        env["HOME"] = "/app/home"
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, env=env, input=input_text,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"Command timed out after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "returncode": -1}


DEFAULT_DUMP_DIR = "/app/data/gtv_adb_shuffle"
HOST_DUMP_DIR = "/home/tbosley02/media-search-app/data/gtv_adb_shuffle"
LR_CLIENT_ID = "f1ffa6f2e5d19010-com-plexapp-android"


def _dump_dir() -> Path:
    for p in (DEFAULT_DUMP_DIR, HOST_DUMP_DIR, "/tmp/gtv_adb_shuffle"):
        path = Path(p)
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except Exception:
            continue
    return Path("/tmp")


def _target(device_ip: str, adb_port: int) -> str:
    return f"{device_ip}:{int(adb_port)}"


def _adb(target: str, *shell_args: str, timeout: int = 15) -> dict:
    return run_adb(["adb", "-s", target, "shell", *shell_args], timeout=timeout)


def _ensure_connected(device_ip: str, adb_port: int) -> str:
    target = _target(device_ip, adb_port)
    # Only connect this device — never disconnect others.
    run_adb(["adb", "connect", target], timeout=8)
    time.sleep(0.3)
    probe = run_adb(["adb", "-s", target, "shell", "echo", "ok"], timeout=5)
    if not (probe.get("ok") and "ok" in (probe.get("stdout") or "")):
        raise RuntimeError(f"ADB not ready for {target}: {probe.get('stderr')}")
    return target


def _focus(target: str) -> str:
    out = _adb(target, "dumpsys", "window", timeout=8).get("stdout") or ""
    for line in out.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            return line.strip()
    return ""


def _uidump(target: str, label: str) -> Path:
    remote = "/sdcard/uidump_gtv_shuffle.xml"
    _adb(target, "uiautomator", "dump", remote, timeout=20)
    local = _dump_dir() / f"{label}_{int(time.time())}.xml"
    pull = run_adb(["adb", "-s", target, "pull", remote, str(local)], timeout=15)
    if not local.exists():
        # Fallback: cat via shell
        raw = _adb(target, "cat", remote, timeout=15).get("stdout") or ""
        local.write_text(raw, encoding="utf-8", errors="replace")
    if not pull.get("ok") and not local.exists():
        raise RuntimeError(f"uiautomator dump failed: {pull}")
    return local


def _parse_nodes(xml_path: Path) -> list[dict]:
    try:
        root = ET.parse(xml_path).getroot()
    except Exception as e:
        logger.warning("Bad UI dump %s: %s", xml_path, e)
        return []
    nodes = []
    for n in root.iter("node"):
        b = n.attrib.get("bounds") or ""
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        nodes.append({
            "text": n.attrib.get("text") or "",
            "desc": n.attrib.get("content-desc") or "",
            "id": n.attrib.get("resource-id") or "",
            "cls": n.attrib.get("class") or "",
            "clickable": n.attrib.get("clickable") == "true",
            "focusable": n.attrib.get("focusable") == "true",
            "focused": n.attrib.get("focused") == "true",
            "bounds": (x1, y1, x2, y2),
            "cx": (x1 + x2) // 2,
            "cy": (y1 + y2) // 2,
        })
    return nodes


def _tap(target: str, x: int, y: int) -> None:
    _adb(target, "input", "tap", str(x), str(y), timeout=5)


def _key(target: str, keycode: str) -> None:
    _adb(target, "input", "keyevent", keycode, timeout=5)


def _find_text(nodes: list[dict], text: str, exact: bool = True) -> Optional[dict]:
    tnorm = text.strip().lower()
    for n in nodes:
        val = (n["text"] or n["desc"]).strip()
        if not val:
            continue
        if exact and val.lower() == tnorm:
            return n
        if not exact and tnorm in val.lower():
            return n
    return None


def _clickable_covering(nodes: list[dict], inner: dict) -> dict:
    ix1, iy1, ix2, iy2 = inner["bounds"]
    icx, icy = inner["cx"], inner["cy"]
    candidates = []
    for n in nodes:
        if not n["clickable"]:
            continue
        x1, y1, x2, y2 = n["bounds"]
        if x1 <= icx <= x2 and y1 <= icy <= y2:
            area = max(1, (x2 - x1) * (y2 - y1))
            candidates.append((area, n))
    if not candidates:
        return inner
    candidates.sort(key=lambda t: t[0])
    return candidates[0][1]


def _wait_focus(target: str, substr: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if substr in _focus(target):
            return True
        time.sleep(0.5)
    return False


def _sanitize_search(title: str) -> str:
    # ADB `input text` hates spaces/specials — use %s for space.
    cleaned = re.sub(r"[^A-Za-z0-9 ]+", "", title).strip()
    # Prefer a distinctive short query for The Office (US) etc.
    cleaned = cleaned.replace("  ", " ")
    return cleaned[:40] or title[:40]


def _open_plex_home(target: str) -> None:
    _key(target, "KEYCODE_WAKEUP")
    time.sleep(0.5)
    _key(target, "KEYCODE_HOME")
    time.sleep(0.5)
    _adb(target, "am", "force-stop", "com.plexapp.android", timeout=8)
    time.sleep(1.0)
    run_adb([
        "adb", "-s", target, "shell",
        "monkey", "-p", "com.plexapp.android",
        "-c", "android.intent.category.LAUNCHER", "1",
    ], timeout=10)
    _wait_focus(target, "HomeActivityTV", timeout=15) or _wait_focus(target, "plexapp", timeout=10)
    time.sleep(1.5)


def _reveal_sidebar(target: str) -> None:
    """Plex TV hides the nav rail when focus is in content — peek it with LEFT."""
    for _ in range(8):
        _key(target, "KEYCODE_DPAD_LEFT")
        time.sleep(0.2)
    time.sleep(0.4)


def _open_search(target: str) -> None:
    # Sidebar is often collapsed on home hero / continue-watching.
    _reveal_sidebar(target)
    path = _uidump(target, "home_before_search")
    nodes = _parse_nodes(path)
    search = _find_text(nodes, "Search", exact=True)
    if not search:
        # One more reveal + dump
        _reveal_sidebar(target)
        path = _uidump(target, "home_before_search_retry")
        nodes = _parse_nodes(path)
        search = _find_text(nodes, "Search", exact=True)

    if search:
        hit = _clickable_covering(nodes, search)
        logger.info("Opening Search via tap (%s,%s)", hit["cx"], hit["cy"])
        _tap(target, hit["cx"], hit["cy"])
    else:
        logger.warning("Search label missing; trying fixed sidebar coords")
        _tap(target, 200, 160)

    time.sleep(2.0)
    if "Search" not in _focus(target):
        _tap(target, 208, 162)
        time.sleep(1.5)
    if "Search" not in _focus(target):
        # DPAD: move up from Home to Search then select
        for _ in range(4):
            _key(target, "KEYCODE_DPAD_UP")
            time.sleep(0.25)
        _key(target, "KEYCODE_DPAD_CENTER")
        time.sleep(1.5)

    if "Search" not in _focus(target):
        raise RuntimeError(f"Failed to open Plex Search; focus={_focus(target)}")


def _type_query(target: str, query: str) -> None:
    # Clear any existing query with backspaces, then type.
    for _ in range(24):
        _key(target, "KEYCODE_DEL")
    time.sleep(0.2)
    encoded = _sanitize_search(query).replace(" ", "%s")
    if encoded:
        _adb(target, "input", "text", encoded, timeout=10)
    time.sleep(2.5)


def _pick_show_result(nodes: list[dict], show_title: str) -> Optional[dict]:
    """Pick the Show result card matching show_title (prefer exact)."""
    title_l = show_title.strip().lower()
    # Collect title text nodes and nearby "· Show" markers
    title_nodes = []
    for n in nodes:
        t = n["text"].strip()
        if not t:
            continue
        if t.lower() == title_l or title_l in t.lower():
            title_nodes.append(n)
    if not title_nodes:
        return None

    def score(n: dict) -> tuple:
        exact = 0 if n["text"].strip().lower() == title_l else 1
        # Prefer entries with a nearby "Show" type label at similar y
        has_show = 1
        x1, y1, x2, y2 = n["bounds"]
        for m in nodes:
            mt = m["text"]
            if "· Show" in mt or mt.endswith("Show"):
                my = m["bounds"][1]
                if abs(my - y1) < 80 and abs(m["bounds"][0] - x1) < 80:
                    has_show = 0
                    break
        # Prefer upper results
        return (exact, has_show, y1, x1)

    title_nodes.sort(key=score)
    best = title_nodes[0]
    return _clickable_covering(nodes, best)


def _tap_more_then_shuffle(target: str, retries: int = 3) -> bool:
    for attempt in range(retries):
        path = _uidump(target, f"details_before_more_{attempt}")
        nodes = _parse_nodes(path)
        # If Shuffle already visible (overlay), tap it
        sh = _find_text(nodes, "Shuffle", exact=True)
        if sh:
            hit = _clickable_covering(nodes, sh)
            _tap(target, hit["cx"], hit["cy"])
            time.sleep(2.0)
            return True

        # Action chips: small clickable views in the metadata action row
        chips = []
        for n in nodes:
            if not n["clickable"]:
                continue
            x1, y1, x2, y2 = n["bounds"]
            w, h = x2 - x1, y2 - y1
            if 450 <= y1 <= 600 and 60 <= w <= 140 and 60 <= h <= 120 and x1 >= 350:
                chips.append(n)
        chips.sort(key=lambda n: n["bounds"][0])
        if not chips:
            # Broader fallback: any clickable in band
            for n in nodes:
                if not n["clickable"]:
                    continue
                x1, y1, x2, y2 = n["bounds"]
                if 450 <= y1 <= 620 and x1 >= 380 and (x2 - x1) < 200:
                    chips.append(n)
            chips.sort(key=lambda n: n["bounds"][0])

        if not chips:
            logger.warning("No action chips found (attempt %s)", attempt)
            time.sleep(1)
            continue

        more = chips[-1]  # rightmost = More (...)
        logger.info("Tapping More chip (%s,%s) among %d chips", more["cx"], more["cy"], len(chips))
        _tap(target, more["cx"], more["cy"])
        time.sleep(1.5)
        path2 = _uidump(target, f"more_menu_{attempt}")
        nodes2 = _parse_nodes(path2)
        sh = _find_text(nodes2, "Shuffle", exact=True)
        if not sh:
            # Known 1080p More button center from Living Room layout
            logger.warning("Shuffle not in More menu (attempt %s); retry fixed coords", attempt)
            _key(target, "KEYCODE_BACK")
            time.sleep(0.5)
            _tap(target, 784, 518)
            time.sleep(1.5)
            path2 = _uidump(target, f"more_menu_fixed_{attempt}")
            nodes2 = _parse_nodes(path2)
            sh = _find_text(nodes2, "Shuffle", exact=True)
        if not sh:
            logger.warning("Shuffle still missing (attempt %s)", attempt)
            _key(target, "KEYCODE_BACK")
            time.sleep(0.8)
            continue
        hit = _clickable_covering(nodes2, sh)
        logger.info("Tapping Shuffle at (%s,%s)", hit["cx"], hit["cy"])
        _tap(target, hit["cx"], hit["cy"])
        time.sleep(2.5)
        return True
    return False


def _verify_session(
    plex_url: str,
    plex_token: str,
    device_ip: str,
    show_title: str,
    client_id: Optional[str] = None,
    timeout: float = 25.0,
) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            r = requests.get(
                f"{plex_url.rstrip('/')}/status/sessions",
                params={"X-Plex-Token": plex_token},
                timeout=8,
            )
            root = ET.fromstring(r.content)
            for video in list(root):
                player = video.find("Player")
                if player is None:
                    continue
                addr = player.attrib.get("address") or ""
                mid = player.attrib.get("machineIdentifier") or ""
                if addr != device_ip and mid != (client_id or LR_CLIENT_ID):
                    continue
                gp = video.attrib.get("grandparentTitle") or ""
                title = video.attrib.get("title") or ""
                state = player.attrib.get("state") or ""
                last = {
                    "grandparentTitle": gp,
                    "title": title,
                    "state": state,
                    "ratingKey": video.attrib.get("ratingKey"),
                    "parentIndex": video.attrib.get("parentIndex"),
                    "index": video.attrib.get("index"),
                    "machineIdentifier": mid,
                    "address": addr,
                }
                # Match show title loosely
                if state in ("playing", "paused", "buffering"):
                    if not show_title or show_title.lower() in (gp or title).lower() or (gp or title).lower() in show_title.lower():
                        last["ok"] = True
                        return last
        except Exception as e:
            last = {"ok": False, "error": str(e)}
        time.sleep(1.0)
    last["ok"] = False
    return last


def resolve_shuffle_target(plex_url: str, plex_token: str, rating_key: int | str) -> dict:
    """Return {type, title, show_title, season_index, rating_key} for UI navigation."""
    r = requests.get(
        f"{plex_url.rstrip('/')}/library/metadata/{rating_key}",
        params={"X-Plex-Token": plex_token},
        headers={"Accept": "application/xml"},
        timeout=10,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)
    node = None
    for tag in ("Directory", "Video"):
        node = root.find(tag)
        if node is not None:
            break
    if node is None and len(list(root)):
        node = list(root)[0]
    if node is None:
        raise RuntimeError(f"No metadata for ratingKey={rating_key}")
    typ = node.attrib.get("type") or ""
    title = node.attrib.get("title") or ""
    show_title = title
    season_index = None
    if typ == "season":
        show_title = node.attrib.get("parentTitle") or node.attrib.get("grandparentTitle") or title
        season_index = node.attrib.get("index")
    elif typ == "episode":
        show_title = node.attrib.get("grandparentTitle") or title
        season_index = node.attrib.get("parentIndex")
    return {
        "type": typ,
        "title": title,
        "show_title": show_title,
        "season_index": season_index,
        "rating_key": str(rating_key),
    }


def _open_season_if_needed(target: str, season_index: Optional[str]) -> None:
    if not season_index:
        return
    path = _uidump(target, "show_for_season")
    nodes = _parse_nodes(path)
    # Season posters are labeled in content-desc or nearby; Plex often uses
    # image containers under Seasons row. Try matching "Season N" text first.
    label = f"Season {season_index}"
    # Also Roman / bare number posters — tap by unwatched badge position is fragile.
    # Prefer text "Season One" style — on this TV dump seasons were image-only.
    # Fallback: seasons row FrameLayouts left-to-right roughly season order as shown.
    season_frames = []
    for n in nodes:
        if n["id"].endswith("image_container") or (
            n["clickable"] and n["bounds"][1] >= 750 and n["bounds"][0] >= 400 and (n["bounds"][2] - n["bounds"][0]) >= 200
        ):
            if n["bounds"][1] >= 750:
                season_frames.append(n)
    # Dedup overlapping
    season_frames.sort(key=lambda n: n["bounds"][0])
    uniq = []
    for n in season_frames:
        if not uniq or abs(n["bounds"][0] - uniq[-1]["bounds"][0]) > 40:
            uniq.append(n)
    try:
        idx = int(season_index) - 1
    except Exception:
        idx = -1
    if 0 <= idx < len(uniq):
        hit = uniq[idx]
        logger.info("Opening season index %s via poster tap (%s,%s)", season_index, hit["cx"], hit["cy"])
        _tap(target, hit["cx"], hit["cy"])
        time.sleep(3.0)


def start_native_shuffle(
    device_ip: str,
    adb_port: int,
    show_title: str,
    *,
    plex_url: Optional[str] = None,
    plex_token: Optional[str] = None,
    client_id: Optional[str] = None,
    season_index: Optional[str] = None,
    verify: bool = True,
) -> dict:
    """Drive Living-Room-style GTV Plex UI to native Shuffle.

    Returns dict with ok/success, method, session info, dumps path hints.
    """
    if not device_ip:
        return {"ok": False, "success": False, "error": "device_ip required"}

    target = _ensure_connected(device_ip, adb_port)
    logger.info("GTV ADB native shuffle on %s for %r season=%s", target, show_title, season_index)

    _open_plex_home(target)
    _open_search(target)
    _type_query(target, show_title)

    path = _uidump(target, "search_results")
    nodes = _parse_nodes(path)
    card = _pick_show_result(nodes, show_title)
    if not card:
        # Try shortened title (strip parenthetical)
        short = re.sub(r"\s*\([^)]*\)\s*", " ", show_title).strip()
        if short and short.lower() != show_title.lower():
            card = _pick_show_result(nodes, short)
    if not card:
        return {
            "ok": False,
            "success": False,
            "error": f"Show result not found in Plex search for {show_title!r}",
            "dump": str(path),
        }

    logger.info("Tapping show result at (%s,%s)", card["cx"], card["cy"])
    _tap(target, card["cx"], card["cy"])
    time.sleep(4.0)
    # Should be on show details (HomeActivityTV with metadata), not player
    if "PlayerActivity" in _focus(target):
        _key(target, "KEYCODE_BACK")
        time.sleep(2.0)

    # Confirm we are on a show/season details page (not home hero)
    details = _uidump(target, "show_details")
    dnodes = _parse_nodes(details)
    texts = {(n["text"] or "").strip() for n in dnodes}
    if "Seasons" not in texts and "Season" not in " ".join(texts) and not any(
        t == show_title or show_title.lower() in t.lower() for t in texts if t
    ):
        logger.warning("Details page unexpected; texts sample=%s", list(texts)[:20])

    _open_season_if_needed(target, season_index)

    if not _tap_more_then_shuffle(target):
        return {
            "ok": False,
            "success": False,
            "error": "Could not find/tap Shuffle in More menu",
            "focus": _focus(target),
        }

    # Player should come up
    playing_ui = _wait_focus(target, "PlayerActivity", timeout=20)
    session = {}
    if verify and plex_url and plex_token:
        session = _verify_session(
            plex_url, plex_token, device_ip, show_title,
            client_id=client_id, timeout=25,
        )
    else:
        session = {"ok": playing_ui, "ui_player": playing_ui}

    ok = bool(session.get("ok") or playing_ui)
    return {
        "ok": ok,
        "success": ok,
        "method": "adb_ui_native_shuffle",
        "device": target,
        "show_title": show_title,
        "season_index": season_index,
        "ui_player": playing_ui,
        "session": session,
        "dump_dir": str(_dump_dir()),
    }


def start_native_shuffle_for_rating_key(
    device_ip: str,
    adb_port: int,
    rating_key: int | str,
    plex_url: str,
    plex_token: str,
    *,
    client_id: Optional[str] = None,
) -> dict:
    meta = resolve_shuffle_target(plex_url, plex_token, rating_key)
    season_index = meta["season_index"] if meta["type"] == "season" else None
    # For episode keys, shuffle at show level (series shuffle)
    return start_native_shuffle(
        device_ip,
        adb_port,
        meta["show_title"],
        plex_url=plex_url,
        plex_token=plex_token,
        client_id=client_id,
        season_index=season_index,
        verify=True,
    )


def power_off_tv(device_ip: str, adb_port: int) -> dict:
    """Sleep/power-off ONLY the given TV."""
    target = _ensure_connected(device_ip, adb_port)
    # Prefer SLEEP; fallback POWER
    r1 = _adb(target, "input", "keyevent", "KEYCODE_SLEEP", timeout=5)
    time.sleep(1.5)
    state = run_adb(
        ["adb", "-s", target, "shell", "dumpsys", "power"],
        timeout=8,
    ).get("stdout") or ""
    awake = "mWakefulness=Awake" in state
    if awake:
        _adb(target, "input", "keyevent", "KEYCODE_POWER", timeout=5)
        time.sleep(1.5)
        state = run_adb(
            ["adb", "-s", target, "shell", "dumpsys", "power"],
            timeout=8,
        ).get("stdout") or ""
        awake = "mWakefulness=Awake" in state
    wake = "unknown"
    for line in state.splitlines():
        if "mWakefulness=" in line:
            wake = line.strip().split("=", 1)[-1]
            break
    return {"ok": not awake, "wakefulness": wake, "target": target, "sleep_cmd_ok": r1.get("ok")}


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="GTV ADB native Plex Shuffle")
    ap.add_argument("--ip", default="192.168.1.108")
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--rating-key", default="37955")
    ap.add_argument("--title", default="")
    ap.add_argument("--plex-url", default=os.environ.get("PLEX_URL", "http://192.168.1.25:32400"))
    ap.add_argument("--plex-token", default=os.environ.get("PLEX_TOKEN", ""))
    ap.add_argument("--power-off", action="store_true")
    ap.add_argument("--client-id", default=LR_CLIENT_ID)
    args = ap.parse_args()

    if not args.plex_token:
        env_path = Path("/home/tbosley02/media-search-app/.env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("PLEX_TOKEN="):
                    args.plex_token = line.split("=", 1)[1].strip().strip("\"'")
                    break
        if not args.plex_token:
            tpath = Path("/tmp/plex_token.txt")
            if tpath.exists():
                args.plex_token = tpath.read_text().strip()

    if args.power_off:
        print(power_off_tv(args.ip, args.port))
        sys.exit(0)

    if args.title:
        result = start_native_shuffle(
            args.ip, args.port, args.title,
            plex_url=args.plex_url, plex_token=args.plex_token,
            client_id=args.client_id,
        )
    else:
        result = start_native_shuffle_for_rating_key(
            args.ip, args.port, args.rating_key,
            args.plex_url, args.plex_token,
            client_id=args.client_id,
        )
    print(result)
    sys.exit(0 if result.get("ok") else 1)
