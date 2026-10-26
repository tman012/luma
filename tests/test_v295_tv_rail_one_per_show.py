"""v295: Recently added TV — one card per show (never episode rating_key)."""
import os
import re
import xml.etree.ElementTree as ET

from app.cache_updater import collapse_recent_tv_episodes
from app import home_rails


def test_sw_v295_or_newer():
    root = os.path.join(os.path.dirname(__file__), '..')
    sw = open(os.path.join(root, 'static', 'sw.js')).read()
    stamp = open(os.path.join(root, 'static', 'sw-stamp.json')).read()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 295
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp


def _ep(rk, title='Bluey', show_rk=695, added=0, **extra):
    row = {
        'type': 'episode',
        'rating_key': rk,
        'title': f'Episode {rk}',
        'show_title': title,
        'show_rating_key': show_rk,
        'show_year': 2018,
        'added_at': added,
        'addedAt': added,
    }
    row.update(extra)
    return row


def test_collapse_52_bluey_same_show_rk_one_card():
    # Newest added_at first after sort — ep 52 newest
    items = [_ep(1000 + i, show_rk=695, added=1_700_000_000 + i) for i in range(52)]
    out = collapse_recent_tv_episodes(items, limit=30)
    assert len(out) == 1
    assert out[0]['show_rating_key'] == 695
    assert out[0]['show_title'] == 'Bluey'
    assert out[0]['latest_added_at'] == 1_700_000_000 + 51
    assert '/library/metadata/695/thumb' in out[0]['poster_url']


def test_collapse_missing_show_rk_same_title_one_card():
    items = [
        _ep(2000 + i, show_rk=None, added=1_700_000_100 + i)
        for i in range(52)
    ]
    out = collapse_recent_tv_episodes(items, limit=30)
    assert len(out) == 1
    assert out[0]['show_title'] == 'Bluey'
    # Never keyed on episode rating_key — synthetic negative PK, not 2000..2051
    assert out[0]['show_rating_key'] < 0
    assert out[0]['latest_added_at'] == 1_700_000_100 + 51


def test_collapse_two_shows_two_cards_newest_first():
    items = (
        [_ep(3000 + i, title='Bluey', show_rk=695, added=100 + i) for i in range(10)]
        + [_ep(4000 + i, title='Andor', show_rk=900, added=200 + i) for i in range(5)]
    )
    out = collapse_recent_tv_episodes(items, limit=30)
    assert len(out) == 2
    assert out[0]['show_title'] == 'Andor'  # newest added_at
    assert out[0]['show_rating_key'] == 900
    assert out[1]['show_title'] == 'Bluey'
    assert out[1]['show_rating_key'] == 695


def test_collapse_skips_non_episode_and_identityless():
    items = [
        {'type': 'movie', 'rating_key': 1, 'title': 'Heat', 'added_at': 999},
        {'type': 'episode', 'rating_key': 2, 'title': '', 'show_title': '',
         'show_rating_key': None, 'added_at': 998},
        _ep(3, title='Severance', show_rk=50, added=997),
    ]
    out = collapse_recent_tv_episodes(items, limit=30)
    assert len(out) == 1
    assert out[0]['show_title'] == 'Severance'


def test_collapse_never_uses_episode_rating_key_as_show():
    # Bug regression: show_rating_key missing must NOT fall back to episode rk
    items = [_ep(i, show_rk=None, added=i) for i in range(1, 6)]
    out = collapse_recent_tv_episodes(items, limit=30)
    assert len(out) == 1
    assert out[0]['show_rating_key'] not in {1, 2, 3, 4, 5}


def test_home_rails_one_card_per_show_bluey_flood(test_app):
    # 52 Bluey episodes + 1 Andor → 2 cards, Bluey once
    vids = []
    for i in range(52):
        vids.append(
            f'<Video ratingKey="{100+i}" type="episode" title="Ep{i}" '
            f'grandparentTitle="Bluey" grandparentRatingKey="695" '
            f'parentIndex="1" index="{i+1}" '
            f'grandparentThumb="/library/metadata/695/thumb/1" addedAt="{1000+i}"/>'
        )
    vids.append(
        '<Video ratingKey="9001" type="episode" title="Kassa" '
        'grandparentTitle="Andor" grandparentRatingKey="900" '
        'parentIndex="1" index="1" '
        'grandparentThumb="/library/metadata/900/thumb/1" addedAt="2000"/>'
    )
    root = ET.fromstring(f'<MediaContainer>{"".join(vids)}</MediaContainer>')
    with test_app.app_context():
        items = home_rails._items(root)
        assert len(items) == 53
        out = home_rails._one_card_per_show(items)
    titles = [c['title'] for c in out]
    assert titles.count('Bluey') == 1
    assert 'Andor' in titles
    assert len(out) == 2
    # Document order: first Bluey kept, then Andor when first seen.
    assert out[0]['title'] == 'Bluey'
    assert out[1]['title'] == 'Andor'


def test_home_rails_missing_grandparent_rating_key_same_title(test_app):
    vids = [
        f'<Video ratingKey="{100+i}" type="episode" title="Ep{i}" '
        f'grandparentTitle="Bluey" parentIndex="1" index="{i+1}"/>'
        for i in range(10)
    ]
    root = ET.fromstring(f'<MediaContainer>{"".join(vids)}</MediaContainer>')
    with test_app.app_context():
        out = home_rails._one_card_per_show(home_rails._items(root))
    assert len(out) == 1
    assert out[0]['title'] == 'Bluey'


def test_cache_updater_source_never_falls_back_to_episode_rk():
    root = os.path.join(os.path.dirname(__file__), '..')
    src = open(os.path.join(root, 'app', 'cache_updater.py')).read()
    assert 'collapse_recent_tv_episodes' in src
    # Old bug line must be gone
    assert "item.get('show_rating_key') or item.get('rating_key')" not in src


def test_use_home_rails_wires_dedupe_helper():
    root = os.path.join(os.path.dirname(__file__), '..')
    src = open(os.path.join(root, 'frontend', 'src', 'features', 'home', 'useHomeRails.ts')).read()
    assert "from './recentTvDedupe'" in src
    assert 'dedupeRecentTvRows' in src


def test_recent_tv_dedupe_ts_present():
    root = os.path.join(os.path.dirname(__file__), '..')
    path = os.path.join(root, 'frontend', 'src', 'features', 'home', 'recentTvDedupe.ts')
    assert os.path.isfile(path)
    body = open(path).read()
    assert 'dedupeRecentTvRows' in body
    assert 'rk > 0' in body
