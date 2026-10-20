"""v208 Home Watchlist rail on test: prod's rail (soonest digital release first, 25 max,
digital-date label under the poster) from the native watchlist, listed with a switch in
Home screen settings like the other rails."""
from pathlib import Path

import pytest

from app import digital_release_cache as drc, home_rails, instance, movie_watchlist
from tests.test_home_rails import REMOTE, _client, plex  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ROWS = [
    {'tmdb_id': 1, 'title': 'No Date', 'year': 2027, 'profile_id': 1, 'added_at': 5},
    {'tmdb_id': 2, 'title': 'Later', 'year': 2026, 'profile_id': 1, 'added_at': 4},
    {'tmdb_id': 3, 'title': 'Soonest', 'year': 2026, 'profile_id': 1, 'added_at': 3},
]
DATES = {1: None, 2: '2026-12-25', 3: '2026-11-03'}


@pytest.fixture
def wl(monkeypatch):
    monkeypatch.setattr(instance, 'is_test', lambda: True)
    monkeypatch.setattr(movie_watchlist, 'movies', lambda: [dict(r) for r in ROWS])
    monkeypatch.setattr(drc, 'get_digital_dates', lambda ids, **k: {i: DATES.get(i) for i in ids})
    monkeypatch.setitem(home_rails._cfg, 'fetch_poster', lambda t: f'/p{t}.jpg')
    monkeypatch.setitem(home_rails._cfg, 'trakt_connected', lambda: False)


def test_soonest_first_with_label_and_poster(wl):
    items = home_rails.watchlist_items()
    assert [i['title'] for i in items] == ['Soonest', 'Later', 'No Date']
    assert [i['label'] for i in items] == ['Nov 3', 'Dec 25', None]
    assert items[0]['tmdb_id'] == 3 and items[0]['type'] == 'movie' and items[0]['subtitle'] == '2026'
    assert items[0]['poster'] == home_rails.TMDB_POSTER + '/p3.jpg'


def test_at_most_25(wl, monkeypatch):
    monkeypatch.setattr(movie_watchlist, 'movies', lambda: [{'tmdb_id': i, 'title': str(i)} for i in range(1, 40)])
    assert len(home_rails.watchlist_items()) == 25


def test_listed_only_on_test(wl, monkeypatch):
    assert home_rails.extra_rails() == [{'id': 'watchlist', 'title': 'Watchlist', 'type': 'movie', 'source': 'watchlist'}]
    monkeypatch.setattr(instance, 'is_test', lambda: False)
    assert home_rails.extra_rails() == []


def test_settings_switch_order_and_home(wl, plex, test_app):  # noqa: F811
    c = _client(test_app)
    libs = c.get('/api/home/libraries', environ_base=REMOTE).get_json()['libraries']
    assert libs[-1] == {**libs[-1], 'id': 'watchlist', 'title': 'Watchlist', 'enabled': True}
    rails = c.get('/api/home', environ_base=REMOTE).get_json()['rails']
    wr = next(r for r in rails if r['id'] == 'watchlist')
    assert wr['title'] == 'Watchlist' and wr['items'][0]['label'] == 'Nov 3'
    # Moved first and switched off: kept on save, gone from Home.
    rows = [{'id': 'watchlist', 'enabled': False}] + [{'id': l['id'], 'enabled': True} for l in libs if l['id'] != 'watchlist']
    assert c.put('/api/home/libraries', json={'libraries': rows}, environ_base=REMOTE,
                 headers={'Origin': 'http://localhost'}).status_code == 200
    libs2 = c.get('/api/home/libraries', environ_base=REMOTE).get_json()['libraries']
    assert libs2[0]['id'] == 'watchlist' and libs2[0]['enabled'] is False
    assert all(r['id'] != 'watchlist' for r in c.get('/api/home', environ_base=REMOTE).get_json()['rails'])


def test_card_shows_label_above_title():
    src = (ROOT / 'frontend/src/components/home/Rails.tsx').read_text()
    assert '{card.label && <div style={cardLabel}' in src
    assert src.index('cardLabel}') < src.index('{card.title}</div>')
    assert 'fontSize: 9, fontWeight: 600' in src


def test_watchlist_cards_have_no_ownership_dot():
    src = (ROOT / 'frontend/src/components/home/Rails.tsx').read_text()
    assert "!c.key.startsWith('watchlist-') ? own[c.key] : undefined" in src


def test_v212_date_label_all_caps_like_prod_with_guard_exemption():
    """Prod 1b4ea74 upper-cases the rail's digital-date label ('NOV 3'); test matches, exempted in the guard."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    r = (root / 'frontend/src/components/home/Rails.tsx').read_text()
    i = r.index('const wlLabel')
    assert "textTransform: 'uppercase'" in r[i:r.index('};', i)]
    assert r.count("textTransform: 'uppercase'") == 1
    g = (root / 'frontend/tests/sentenceCaseGuard.test.ts').read_text()
    assert 'PROD_MATCHED_UPPERCASE' in g and 'prodUpper' in g


def test_v213_watchlist_rail_is_prods_card_flat_navy_and_font():
    """Designer: prod 1b4ea74's AuthenticatedHomePage poster card values, no border, flat navy page, prod's font stack."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    r = (root / 'frontend/src/components/home/Rails.tsx').read_text()
    for v in ("width: 116, height: 174, borderRadius: 10", "marginTop: 6, width: 116",
              "margin: '0 0 2px', fontSize: 10, fontWeight: 600, letterSpacing: '0.04em'",
              "margin: 0, fontSize: 12, fontWeight: 500", "margin: '2px 0 0', fontSize: 10, color: 'rgba(255,255,255,0.38)'",
              'ui-sans-serif, system-ui, sans-serif', "prod={r.id === 'watchlist'}"):
        assert v in r, v
    i = r.index('const wlPoster'); assert 'border' not in r[i:r.index('};', i)].replace('borderRadius', '')
    h = (root / 'frontend/src/pages/MinimalHomePage.tsx').read_text()
    assert "const page: React.CSSProperties = { ...homePage, background: 'var(--color-kiosk-black)'" in h
