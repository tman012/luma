"""cw-logo: Continue Watching cards name the TMDb id of what a tap opens (tmdb_hint), for every item type, so the sheet
starts the clearlogo at the tap; /api/clearlogo answers are never browser-cached."""
import xml.etree.ElementTree as ET
from unittest import mock

import pytest

from app import home_rails, tv_plex
import app.main as m


def el(xml):
    return ET.fromstring(xml)


@pytest.fixture(autouse=True)
def _shows():
    tv_plex._shows.clear()
    yield
    tv_plex._shows.clear()


def test_movie_hint_from_its_guid_tags():
    c = home_rails._card(el('<Video ratingKey="900" type="movie" title="Film" guid="plex://movie/abc">'
                            '<Guid id="imdb://tt1"/><Guid id="tmdb://603"/></Video>'), wide=True)
    assert c['tmdb_hint'] == 603 and c['key'] == '900'


def test_movie_legacy_agent_guid():
    c = home_rails._card(el('<Video ratingKey="901" type="movie" title="Old" guid="com.plexapp.agents.themoviedb://550?lang=en"/>'))
    assert c['tmdb_hint'] == 550


def test_episode_hint_is_the_show_id_from_the_tv_index_never_the_episode_id():
    tv_plex._shows['4501'] = (6191, 73587)
    c = home_rails._card(el('<Video ratingKey="4591" type="episode" grandparentRatingKey="4501" grandparentTitle="Show" '
                            'parentIndex="1" index="2" guid="plex://episode/x"><Guid id="tmdb://999999"/></Video>'), wide=True)
    assert c['show_key'] == '4501' and c['tmdb_hint'] == 6191


def test_episode_legacy_grandparent_guid_fallback():
    c = home_rails._card(el('<Video ratingKey="4591" type="episode" grandparentRatingKey="4501" '
                            'grandparentGuid="com.plexapp.agents.themoviedb://6191?lang=en" parentIndex="1" index="2"/>'))
    assert c['tmdb_hint'] == 6191


def test_season_hint_is_the_show_id():
    tv_plex._shows['4501'] = (6191, None)
    c = home_rails._card(el('<Directory ratingKey="4550" type="season" parentRatingKey="4501" parentTitle="Show" title="Season 1"/>'))
    assert c['show_key'] == '4501' and c['tmdb_hint'] == 6191


def test_no_tmdb_id_anywhere_means_no_hint():
    c = home_rails._card(el('<Video ratingKey="4591" type="episode" grandparentRatingKey="4501" '
                            'grandparentGuid="plex://show/abc" parentIndex="1" index="2"/>'))
    assert 'tmdb_hint' not in c


def test_continue_watching_asks_plex_for_guids():
    plex = mock.Mock()
    plex._get.return_value = el('<MediaContainer><Video ratingKey="900" type="movie" title="F"><Guid id="tmdb://603"/></Video></MediaContainer>')
    out = home_rails.continue_watching(plex)
    assert plex._get.call_args.kwargs['params']['includeGuids'] == 1
    assert out[0]['tmdb_hint'] == 603


def test_clearlogo_is_never_browser_cached(test_app):
    client = test_app.test_client()
    for ret in ('/l.png', None):
        with mock.patch.object(m, '_fetch_one_logo', return_value=ret):
            r = client.get('/api/clearlogo/5?type=tv')
        assert r.status_code == 200 and 'no-store' in r.headers.get('Cache-Control', '')
    with mock.patch.object(m, '_fetch_one_logo', side_effect=RuntimeError('x')):
        r = client.get('/api/clearlogo/5?type=tv')
    assert r.status_code == 500 and 'no-store' in r.headers.get('Cache-Control', '')
