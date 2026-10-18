"""v179 regression (Taylor, live v178): week-grid cards are their text and nothing else.

A real browser measures every card at 1440, 1100, 768 and 390: the card's height equals its text
(padding, the show name, the episode line, the air-time line when there is one, and the 1px borders)
within 1.5px, with no image box, no aspect-ratio or min-height anywhere in a card, and day columns
that start at their content height. jsdom has no layout, so only a browser can see a reserved box.

Needs a Luma with TV shows in the calendar week (LUMA_E2E_TV_WEEK, a Sunday, defaults to this week)
and an owner sign-in: LUMA_E2E_USER plus LUMA_E2E_PASSWORD (the /login form) or LUMA_E2E_PIN.
"""
import os
import re

import pytest

from conftest import BASE, PIN, USER, SHOTS

WEEK = os.environ.get("LUMA_E2E_TV_WEEK", "")
PASSWORD = os.environ.get("LUMA_E2E_PASSWORD")
WIDTHS = {1440: (900, False), 1100: (800, False), 768: (1000, False), 390: (844, True)}

MEASURE = r"""() => {
  const px = v => parseFloat(v) || 0;
  return [...document.querySelectorAll('[data-testid=tv-cal-grid] [data-testid=tv-agenda-row]')].map(card => {
    const text = card.querySelector('.tvc-text');
    const cs = getComputedStyle(text);
    const lines = [...text.children].map(k => k.getBoundingClientRect().height);
    const content = lines.reduce((a, b) => a + b, 0) + Math.max(0, lines.length - 1) * px(cs.rowGap)
      + px(cs.paddingTop) + px(cs.paddingBottom) + px(getComputedStyle(card).borderTopWidth) + px(getComputedStyle(card).borderBottomWidth);
    const sized = [card, ...card.querySelectorAll('*')].filter(el => {
      if (el.closest('[data-testid=tv-card-bookmark]') || el.dataset.testid === 'tv-stripe') return false;
      const s = getComputedStyle(el);
      return s.aspectRatio !== 'auto' || px(s.minHeight) > 0;
    }).length;
    return { h: card.getBoundingClientRect().height, content, sized, media: card.querySelectorAll('img,picture,video').length,
      body: card.querySelector('[data-testid=tv-card-body]').children.length };
  });
}"""


def _sign_in(pg):
    if PASSWORD:
        pg.goto(f"{BASE}/login")
        pg.wait_for_load_state("networkidle")
        fields = [x for x in pg.locator("input").all() if x.is_visible() and x.get_attribute("type") not in ("checkbox", "hidden")]
        fields[0].fill(USER)
        fields[1].fill(PASSWORD)
        pg.get_by_role("button", name=re.compile("^Sign in$", re.I)).click()
        pg.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        pg.wait_for_load_state("networkidle")
    else:
        r = pg.request.post(f"{BASE}/api/auth/login", data={"name": USER, "pin": PIN})
        assert r.ok, r.status


@pytest.mark.e2e
@pytest.mark.parametrize("width", list(WIDTHS))
def test_week_grid_cards_are_their_text(browser, width):
    height, phone = WIDTHS[width]
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=phone, has_touch=phone,
                              timezone_id="America/New_York")
    try:
        pg = ctx.new_page()
        _sign_in(pg)
        pg.goto(f"{BASE}/settings/tv?view=calendar" + (f"&week={WEEK}" if WEEK else ""))
        pg.wait_for_load_state("networkidle")
        pg.wait_for_selector("[data-testid=tv-cal-grid], [data-testid=tv-cal-empty]", timeout=15000)
        cards = pg.evaluate(MEASURE)
        if not cards:
            pytest.skip("no episodes in the calendar week")
        if SHOTS:
            pg.locator("[data-testid=tv-cal-grid]").screenshot(path=os.path.join(SHOTS, f"tv-calendar-grid-{width}.png"))
        for c in cards:
            assert c["media"] == 0 and c["sized"] == 0 and c["body"] == 1, c
            assert abs(c["h"] - c["content"]) <= 1.5, c          # no gap: the card is exactly its text
        grid_align = pg.evaluate("() => getComputedStyle(document.querySelector('[data-testid=tv-cal-grid]')).alignItems")
        assert grid_align == "start"
    finally:
        ctx.close()
