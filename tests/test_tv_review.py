"""Tester tv1-8646479 M7/M8: what needs review in a show, in words (sentence case, no jargon)."""
from app import tv_scan
from tests.test_file_management import store  # noqa: F401
from tests.test_tv_libraries import tv_env  # noqa: F401
from tests.test_tv_scan import lib, rows  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401


def _show(title):
    return next(s for s in tv_scan.library_view()['shows'] if s['title'] == title)


def test_severance_lists_its_problems(lib):
    tv_scan.scan_all(now=1000.0)
    s = _show('Severance')
    assert (s['mismatch'], s['unrecognized']) == (2, 1)
    assert s['review'] == ['S02E05 is in Season 1', "S02E40 isn't an episode of this show", '1 file not recognized']


def test_multi_episode_and_season_folder(lib):
    (lib / 'Severance (2022)' / 'Season 02' / 'Severance.S03E01.mkv').write_bytes(b'x')
    (lib / 'Severance (2022)' / 'Season 01' / 'Severance.S02E09E10.mkv').write_bytes(b'x')
    (lib / 'Severance (2022)' / 'Extras' / 'Interview.mkv').write_bytes(b'x')
    tv_scan.scan_all(now=1000.0)
    assert _show('Severance')['review'] == [
        'S02E05 is in Season 1', 'S02E09\u2013E10 is in Season 1', "S02E40 isn't an episode of this show",
        'S03E01 is in Season 2', '2 files not recognized']


def test_daily_show_date_reads_as_a_date(lib):
    (lib / 'The Daily Show' / 'The.Daily.Show.2024.10.04.mkv').write_bytes(b'x')
    tv_scan.scan_all(now=1000.0)
    assert _show('The Daily Show')['review'] == ["Oct 4, 2024 isn't an episode of this show"]


def test_nothing_to_review_is_empty(lib):
    tv_scan.scan_all(now=1000.0)
    assert _show('Dark')['review'] == []


def test_long_lists_are_capped(lib, monkeypatch):
    monkeypatch.setattr(tv_scan, 'REVIEW_MAX', 3)
    tv_scan.scan_all(now=1000.0)
    (lib / 'Severance (2022)' / 'Season 02' / 'Severance.S02E41.mkv').write_bytes(b'x')
    (lib / 'Severance (2022)' / 'Season 02' / 'Severance.S02E42.mkv').write_bytes(b'x')
    tv_scan.scan_all(now=2000.0)
    assert _show('Severance')['review'] == ['S02E05 is in Season 1', "S02E40 isn't an episode of this show",
                                            '2 more files don\u2019t fit an episode', '1 file not recognized']
