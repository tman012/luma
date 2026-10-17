"""Settings > Trackers and Settings > Connectors > qBittorrent, end to end (phone + desktop).

Needs a private Luma (see README) signed in as the owner, a mock Torznab tracker on a
public-looking address (tracker URLs on private addresses are refused by design) and a
mock qBittorrent with /__qa/state (QA harness mock_qbit.py) on two ports:
  LUMA_E2E_TORZNAB_URL   e.g. http://44.0.0.91:7311/api     LUMA_E2E_TORZNAB_KEY
  LUMA_E2E_QBIT_URL      e.g. http://192.168.77.10:8080      LUMA_E2E_QBIT_USER / LUMA_E2E_QBIT_PASS
  LUMA_E2E_QBIT_URL2     a second mock qBit (same login) to prove a stored password never moves
"""
import os, re, time
import pytest
from conftest import BASE

TZ_URL, TZ_KEY = os.environ.get("LUMA_E2E_TORZNAB_URL"), os.environ.get("LUMA_E2E_TORZNAB_KEY")
QB_URL, QB_URL2 = os.environ.get("LUMA_E2E_QBIT_URL"), os.environ.get("LUMA_E2E_QBIT_URL2")
QB_USER, QB_PASS = os.environ.get("LUMA_E2E_QBIT_USER"), os.environ.get("LUMA_E2E_QBIT_PASS")
pytestmark = pytest.mark.skipif(not (TZ_URL and TZ_KEY and QB_URL and QB_URL2 and QB_USER and QB_PASS),
                                reason="set LUMA_E2E_TORZNAB_* and LUMA_E2E_QBIT_* (see module docstring)")
W = {"Origin": BASE}


def _wipe_trackers(page):
    for t in page.request.get(f"{BASE}/api/trackers").json().get("trackers", []):
        page.request.delete(f"{BASE}/api/trackers/{t['id']}", headers=W)


def _releases(page, title="Interstellar", media_type="movie"):
    r = page.request.get(f"{BASE}/api/sources/all-results?title={title}&media_type={media_type}")
    assert r.ok, r.status
    return r.json().get("releases", [])


def _wait_text(page, testid, pattern, timeout=20):
    """The text of a status element once it matches pattern (it may fade later)."""
    end, txt = time.time() + timeout, ""
    while time.time() < end:
        loc = page.get_by_test_id(testid)
        txt = loc.first.inner_text() if loc.count() else ""
        if re.search(pattern, txt):
            return txt.strip()
        time.sleep(0.2)
    raise AssertionError(f"{testid}: no {pattern!r} within {timeout}s (last {txt!r})")


def _open_trackers(page):
    page.goto(f"{BASE}/settings/trackers")
    page.wait_for_load_state("networkidle")


def test_trackers_add_test_save_sources_off_delete(page):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _wipe_trackers(page)
    _open_trackers(page)
    assert page.get_by_text("No trackers", exact=True).is_visible()
    page.get_by_role("button", name="Add tracker").first.click()
    page.get_by_test_id("tracker-type").filter(has_text=re.compile("^Torznab$")).first.click()
    assert page.get_by_test_id("tracker-switch").first.get_attribute("aria-checked") == "true"
    name = f"E2E Torznab {page.viewport_name}"
    page.fill("#tracker-name", name)
    page.fill("#tracker-url", TZ_URL)
    page.fill("#tracker-api_key", "e2e-wrong-key")
    for cat in ("Movies", "TV"):
        chip = page.get_by_test_id("tracker-category").filter(has_text=re.compile(f"^{cat}$")).first
        if chip.get_attribute("aria-pressed") != "true":
            chip.click()
    page.get_by_test_id("tracker-test").click()
    bad = _wait_text(page, "tracker-test-result", r"\S")
    assert bad and bad not in ("Unexpected response", "Failed", "Error"), bad   # a specific reason
    page.fill("#tracker-api_key", TZ_KEY)
    time.sleep(3.5)   # Luma spaces Tests of one tracker 3 s apart ("Rate limited")
    page.get_by_test_id("tracker-test").click()
    assert "Connected" in _wait_text(page, "tracker-test-result", "Connected")
    page.get_by_test_id("tracker-save").click()
    page.get_by_text(name).first.wait_for()
    assert TZ_KEY not in page.content()
    rel = _releases(page)
    assert {re.search(r"(2160p|1080p|720p)", r["title"]).group(1) for r in rel if re.search(r"(2160p|1080p|720p)", r["title"])} >= {"1080p", "720p"}
    # Off: no results.
    _open_trackers(page)
    page.get_by_role("switch").first.click()
    time.sleep(1)
    assert _releases(page) == []
    # Delete: no results, list empty.
    page.get_by_text(name).first.click()
    page.get_by_test_id("tracker-delete").first.click()
    page.get_by_role("button", name="Delete").last.click()
    page.get_by_text("No trackers", exact=True).wait_for()
    assert _releases(page) == []
    assert not errors, errors


@pytest.mark.parametrize("status", [429, 500])
def test_trackers_types_failure_shows_retry(page, status):
    page.route("**/api/trackers/types", lambda route, req: route.fulfill(status=status, body="{}", headers={"Content-Type": "application/json"}))
    _open_trackers(page)
    page.get_by_role("button", name="Retry").wait_for(timeout=5000)


def _qbit_state(page, url):
    return page.request.get(f"{url}/__qa/state").json()


def test_qbit_card_test_save_password_never_returned_or_moved(page):
    page.goto(f"{BASE}/settings/connectors")
    page.wait_for_load_state("networkidle")
    saved = page.request.get(f"{BASE}/api/settings/connectors/qbittorrent").json()
    if not saved["url"]:   # a fresh install starts all empty
        assert all(page.input_value(f"#qbit-{k}") == "" for k in ("url", "username", "password", "category"))
    page.fill("#qbit-url", QB_URL)
    page.fill("#qbit-username", QB_USER)
    page.fill("#qbit-password", "e2e-wrong-password")
    page.fill("#qbit-category", "luma-test")
    page.get_by_test_id("qbit-test").click()
    assert "Wrong username or password" in _wait_text(page, "qbit-result", "Wrong username")
    page.fill("#qbit-password", QB_PASS)
    page.get_by_test_id("qbit-test").click()
    assert "Connected" in _wait_text(page, "qbit-result", "Connected")
    page.get_by_test_id("qbit-save").click()
    time.sleep(1)
    page.reload()
    page.wait_for_load_state("networkidle")
    assert page.input_value("#qbit-url") == QB_URL and page.input_value("#qbit-category") == "luma-test"
    assert page.input_value("#qbit-password") == "" and page.get_attribute("#qbit-password", "placeholder") == "••••••••"
    assert QB_PASS not in page.content()
    j = page.request.get(f"{BASE}/api/settings/connectors/qbittorrent")
    assert j.json()["password"] == {"set": True} and QB_PASS not in j.text()
    # Another URL with the stored password: refused, nothing sent there.
    before = _qbit_state(page, QB_URL2)["logins"]
    r = page.request.post(f"{BASE}/api/settings/connectors/qbittorrent/test", headers=W,
                          data={"url": QB_URL2, "username": QB_USER, "password": "********", "category": "luma-test"})
    assert r.status == 400 and r.json()["fields"]["password"] == "Required"
    assert _qbit_state(page, QB_URL2)["logins"] == before
