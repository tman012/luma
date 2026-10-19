"""v180 (Taylor): prod's library ownership on the :5002 minimal line.

A real browser opens an owned title on a LUMA_MINIMAL Luma at 1440 and 390: prod's owned Play
(desktop movie popup) or Watch is shown, clicking it starts nothing (no request, no player), and
as prod, Home's all-owned Plex library rails carry no owned orb (v181).

Needs a minimal Luma whose Plex library holds LUMA_E2E_OWNED_TITLE (default Arrival) and an
owner sign-in: LUMA_E2E_USER plus LUMA_E2E_PASSWORD (the /login form) or LUMA_E2E_PIN.
"""
import os
import re

import pytest

from conftest import BASE, PIN, USER, SHOTS

TITLE = os.environ.get("LUMA_E2E_OWNED_TITLE", "Arrival")
PASSWORD = os.environ.get("LUMA_E2E_PASSWORD")
WIDTHS = {1440: (900, False), 390: (844, True)}
PLAYBACK = re.compile(r"/api/(plex/(play|stream|transcode|timeline|session)|playback|stream)", re.I)


def _sign_in(pg):
    if PASSWORD:
        pg.goto(f"{BASE}/login")
        pg.wait_for_load_state("networkidle")
        fields = [x for x in pg.locator("input").all() if x.is_visible() and x.get_attribute("type") not in ("checkbox", "hidden")]
        fields[0].fill(USER)
        fields[1].fill(PASSWORD)
        pg.get_by_role("button", name=re.compile("^Sign in$", re.I)).click()
        pg.wait_for_url(lambda u: "/login" not in u, timeout=15000)
    else:
        r = pg.request.post(f"{BASE}/api/auth/login", data={"name": USER, "pin": PIN})
        assert r.ok, r.status
        pg.goto(f"{BASE}/")
    pg.wait_for_load_state("networkidle")


def _open(pg, phone):
    if phone:
        pg.get_by_role("button", name=TITLE, exact=True).first.click()
    else:
        pg.wait_for_selector('[aria-label="Search"]', timeout=15000)
        pg.locator('[aria-label="Search"]').first.click()
        field = pg.locator("[data-search-panel] input").first
        field.wait_for(timeout=10000)
        field.fill(TITLE)
        pg.locator("[data-search-panel]").get_by_text(TITLE, exact=True).first.click()
    pg.wait_for_selector("[data-owned-watch]", state="attached", timeout=15000)


@pytest.mark.e2e
@pytest.mark.parametrize("width", list(WIDTHS))
def test_owned_watch_is_display_only(browser, width):
    height, phone = WIDTHS[width]
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=phone, has_touch=phone,
                              timezone_id="America/New_York")
    try:
        pg = ctx.new_page()
        _sign_in(pg)
        if pg.evaluate("() => window.__LUMA_MINIMAL__ !== true"):
            pytest.skip("not a LUMA_MINIMAL build")
        if phone:
            pg.wait_for_selector("[data-testid^=rail-] button[data-testid^=card-]", timeout=15000)
            pg.wait_for_timeout(1000)
            lib_orbs = pg.evaluate("() => document.querySelectorAll('[data-testid^=rail-] button[data-testid^=card-]:not([data-testid*=tmdb]) [data-owned-orb]').length")
            assert lib_orbs == 0
        _open(pg, phone)
        watch = pg.locator("[data-owned-watch]").first
        btn = watch.locator("button").first if watch.locator("button").count() else watch
        assert btn.is_visible()
        assert btn.inner_text().strip() == ("Watch" if phone else "Play")
        if SHOTS:
            pg.screenshot(path=os.path.join(SHOTS, f"v180-owned-watch-{width}.png"))
        seen = []
        pg.on("request", lambda r: seen.append(r.url) if "/api/" in r.url else None)
        btn.click()
        pg.wait_for_timeout(2000)
        assert not [u for u in seen if PLAYBACK.search(u)], seen
        assert pg.locator('[data-modal="PlaybackModal"], video').count() == 0
        assert btn.is_visible()
    finally:
        ctx.close()
