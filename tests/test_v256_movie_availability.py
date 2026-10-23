"""v256 (PM): Settings > Movies availability (Radarr's minimum availability: announced, in cinemas,
released; default released). The feed grabs a movie, and the scanner searches it, only once it is
available; a feed release no longer marks a movie released. Every tracker and qBit call is a fake."""
from datetime import datetime

import pytest

from app import movie_scanner as sc, movie_settings
from tests.test_v203_movie_scanner import NOW, Eng, _grabs, _scan, mv, rel  # noqa: F401
from tests.test_v203_movie_scanner import guard, owner, store, tv_env, minimal_app, HDR  # noqa: F401

DAY = 86400


def day(t):
    return datetime.fromtimestamp(t, sc.TZ).strftime('%Y-%m-%d')


def dates(tid=693134, **d):
    with movie_settings.connect() as conn:
        sc.ensure(conn)
        sc._set(conn, tid, runtime=166, **d)


def test_the_setting_defaults_to_released_and_is_validated(mv):
    assert movie_settings.view()['availability'] == 'released' == movie_settings.DEFAULT_AVAILABILITY
    for v in ('announced', 'in_cinemas', 'released'):
        assert movie_settings.apply_put({'availability': v}) == {} and movie_settings.view()['availability'] == v
    for bad in ('Released', 'soon', '', None, 1, True, ['released']):
        assert movie_settings.apply_put({'availability': bad}) == {'availability': 'Invalid'}
    assert movie_settings.view()['availability'] == 'released'


def test_available_at_follows_radarr():
    c, dg, ph = '2024-03-01', '2024-04-16', '2024-05-14'
    row = {'cinema_date': c, 'digital_date': dg, 'physical_date': ph}
    assert sc.available_at(row, 'announced') == 0.0
    assert sc.available_at({}, 'announced') == 0.0
    assert sc.available_at(row, 'released') == sc._day_start(dg)                  # the earlier of digital/physical
    assert sc.available_at(dict(row, digital_date=None), 'released') == sc._day_start(ph)
    assert sc.available_at({'cinema_date': c}, 'released') == sc._day_start(c) + 90 * DAY   # cinema + 90 days
    assert sc.available_at({}, 'released') is None                                 # no dates: never
    assert sc.available_at(row, 'in_cinemas') == sc._day_start(c)
    assert sc.available_at({'digital_date': dg}, 'in_cinemas') == sc._day_start(dg)
    assert sc.available_at({}, 'in_cinemas') is None


def test_the_feed_never_grabs_before_the_movie_is_available(mv):
    dates(cinema_date=day(NOW - 10 * DAY))                    # in cinemas 10 days ago, no digital/physical date
    mv.results = {1: [rel('Dune.Part.Two.2024.1080p.WEB-DL-NTb', 10)]}
    assert sc.feed(NOW) == {'trackers': 2, 'grabbed': 0} and _grabs() == []
    assert _scan()['released_at'] is None                     # a feed release no longer marks it released
    movie_settings.apply_put({'availability': 'in_cinemas'})
    mv.now = NOW + sc.FEED_S
    assert sc.feed(NOW + sc.FEED_S)['grabbed'] == 1


def test_announced_grabs_from_the_feed_with_no_dates(mv):
    movie_settings.apply_put({'availability': 'announced'})
    mv.results = {1: [rel('Dune.Part.Two.2024.1080p.WEB-DL-NTb', 10)]}
    assert sc.feed(NOW)['grabbed'] == 1


def test_released_by_cinema_plus_90_days(mv):
    dates(cinema_date=day(NOW - 91 * DAY))
    mv.results = {1: [rel('Dune.Part.Two.2024.1080p.WEB-DL-NTb', 10)]}
    assert sc.feed(NOW)['grabbed'] == 1


def test_searches_wait_for_availability_even_when_scheduled_under_the_old_rule(mv):
    dates(cinema_date=day(NOW - 10 * DAY), next_search_at=NOW - 60, last_search_at=NOW - 3600)  # old feed rule
    assert sc.schedule(NOW) == 0
    assert sc.run_due(NOW)['movies'] == 0 and mv.calls == []
    assert _scan()['next_search_at'] == NOW - 60                # kept: searched as soon as it is available
    dates(digital_date=day(NOW))                              # out today
    assert sc.run_due(NOW)['movies'] == 1


def test_a_never_searched_movie_is_scheduled_once_available(mv):
    dates(physical_date=day(NOW + 2 * DAY))
    assert sc.schedule(NOW) == 0 and _scan()['next_search_at'] is None
    assert sc.schedule(NOW + 3 * DAY) == 1


def test_run_due_skips_a_due_movie_that_is_not_available(mv):
    dates(next_search_at=NOW - 60)                            # due, but no dates under released
    assert sc.run_due(NOW)['movies'] == 0 and mv.calls == []


def test_dates_of_reads_the_cinema_date():
    d = sc.dates_of({'release_date': '2024-02-27', 'release_dates': {'results': [
        {'iso_3166_1': 'US', 'release_dates': [{'type': 2, 'release_date': '2024-02-29T00:00:00.000Z'},
                                               {'type': 3, 'release_date': '2024-03-01T00:00:00.000Z'}]}]}})
    assert d['cinema_date'] == '2024-02-29'
    assert sc.dates_of({'release_date': '2024-02-27'})['cinema_date'] == '2024-02-27'
