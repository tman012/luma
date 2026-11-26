"""A show whose name ends in a number (Blade Runner 2099, 1923, 1883, 9-1-1, 24): the number is part of the
name, not a year, when matching a release to the show (manual search 'Not this show', the auto feed, the
after-download import). Exact full-name matches only: nothing here loosens the matcher."""
import pytest

from app import tv_downloads, tv_match, tv_release, tv_search


def show(title, year, aliases=()):
    names = {tv_match.release_key(title)} | {tv_match.release_key(a) for a in aliases}
    return {'id': 1, 'title': title, 'year': year, 'names': {n for n in names if n}, 'profile': None,
            'series_type': 'standard'}


def eps(season=1, n=8, air='2026-11-24'):
    return [{'id': 100 + i, 'season_number': season, 'episode_number': i, 'air_date': air, 'airstamp': None,
             'runtime': 50, 'has_file': 0} for i in range(1, n + 1)]


BR = show('Blade Runner 2099', 2026, ['\u94f6\u7ffc\u6740\u624b2099'])

# the very releases the trackers returned for Blade Runner 2099 (Nov 26 2026)
NAMES = [
    'Blade Runner 2099 S01 1080p WEBRip 10Bit DDP5 1 x265-NeoNoir',
    'Blade Runner 2099 S01 1080p AMZN WEB-DL DD+ 5.1 Atmos H.264-FLUX',
    'Blade Runner 2099 S01E04 MULTi 1080p WEB H264-UKDTV',
    'Blade Runner 2099 S01E08 FiNAL MULTi 1080p WEB H264-UKDTV',
    'Blade.Runner.2099.S01E01.1080p.WEB.h264-ETHEL',
    'Blade.Runner.2099.2026.S01E01.1080p.WEB.h264-ETHEL',
    'Blade Runner 2099 (2026) S01 1080p AMZN WEB-DL',
]


@pytest.mark.parametrize('name', NAMES)
def test_release_for_a_show_named_with_a_number_matches(name):
    why, ids, season = tv_search.covers(name, BR, eps())
    assert why is None, name
    assert season == 1 and ids


def test_the_parse_itself_is_unchanged():
    # parse() still reads a trailing 4-digit number as a year: the matcher, not the parser, knows the show
    r = tv_release.parse('Blade Runner 2099 S01E04 MULTi 1080p WEB H264-UKDTV')
    assert (r.title, r.year) == ('Blade Runner', 2099)


@pytest.mark.parametrize('name', [
    'Blade Runner 2049 S01E01 1080p WEB',              # a different number is a different show
    'Blade Runner S01E01 1080p WEB',                   # the short name alone is not this show
    'Blade Runner 2099 Rebooted S01E01 1080p WEB',
    'Runner 2099 S01E01 1080p WEB',
    'Blade Runner 20999 S01E01 1080p WEB',
])
def test_near_names_stay_rejected(name):
    why, ids, _ = tv_search.covers(name, BR, eps())
    assert why == tv_search.WRONG_SHOW and ids == []


def test_a_real_year_after_the_numbered_title_is_still_checked():
    why, _, _ = tv_search.covers('Blade Runner 2099 2019 S01E01 1080p WEB', BR, eps())
    assert why == tv_search.WRONG_YEAR


def test_a_plain_title_with_a_wrong_year_is_still_wrong_year():
    other = show('Blade Runner', 2017)
    why, _, _ = tv_search.covers('Blade Runner 2099 S01E01 1080p WEB', other, eps())
    assert why == tv_search.WRONG_YEAR            # 'Blade Runner' + year 2099 is not the 2017 show


@pytest.mark.parametrize('title,year,rel_name', [
    ('1923', 2022, '1923 S02E01 1080p WEB h264-GRP'),
    ('1923', 2022, '1923.2022.S01E01.1080p.WEB.h264-GRP'),
    ('1883', 2021, '1883.S01E01.1080p.WEB.h264-GRP'),
    ('9-1-1', 2018, '9-1-1.S08E01.1080p.WEB.h264-GRP'),
    ('9-1-1', 2018, '9 1 1 S08E01 1080p WEB h264-GRP'),
    ('24', 2001, '24.S01E01.1080p.BluRay.x264-GRP'),
    ('24', 2001, '24 2001 S01E01 1080p BluRay x264-GRP'),
    ('Alien: Earth', 2025, 'Alien Earth 2025 S01E01 1080p WEB h264-GRP'),
    ('The Chosen 2017', 2017, 'The Chosen 2017 S01E01 1080p WEB'),
    ('Fargo', 2014, 'Fargo.2014.S05E01.1080p.WEB.h264-GRP'),
])
def test_other_numbered_and_ordinary_titles_still_match(title, year, rel_name):
    why, ids, _ = tv_search.covers(rel_name, show(title, year), eps(season=int(tv_release.parse(rel_name).season or 1)))
    assert why is None, (title, rel_name)


def test_reading_prefers_the_plain_title_and_never_invents_a_match():
    rel = tv_release.parse('Fargo 2014 S05E01 1080p')
    assert tv_match.release_reading(rel, {'fargo'}) == (True, 2014)
    assert tv_match.release_reading(rel, {'fargo 2014'}) == (True, None)
    assert tv_match.release_reading(rel, {'fargo 2015'})[0] is False
    assert tv_match.release_reading(tv_release.parse('S01E01'), {''})[0] is False


def test_automatic_pick_takes_the_season_pack_for_the_numbered_show():
    want = eps()
    items = [{'title': n, 'seeders': 50, 'size': 6 * 1024 ** 3, 'downloadUrl': 'u', 'indexerId': 1}
             for n in ('Blade Runner 2099 S01 1080p WEBRip 10Bit DDP5 1 x265-NeoNoir',
                       'Blade Runner 2099 S01E01 MULTi 1080p WEB H264-UKDTV')]
    got = tv_search.candidates(items, BR, want)
    assert len(got) == 2
    assert any(c['pack'] and len(c['covers']) == 8 for c in got)


def test_interactive_judge_lists_them_grabbable():
    items = [{'title': 'Blade Runner 2099 S01 1080p AMZN WEB-DL DD+ 5.1 Atmos H.264-FLUX', 'seeders': 193,
              'size': 29664 * 1048576, 'downloadUrl': 'u', 'indexer': 'PrivateHD', 'indexerId': 2}]
    e = eps()
    out = tv_search.judge(items, BR, e, [x['id'] for x in e], set(), set())
    assert out[0]['rejections'] == [] and out[0]['grabbable'] and out[0]['pack']


def test_download_import_finds_the_library_show(monkeypatch, tmp_path):
    import json
    rows = [{'id': 1, 'title': 'Blade Runner 2099', 'year': 2026, 'aliases': '[]', 'path_id': 'p', 'folder': None,
             'season_folder': 1}]

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            return rows

    monkeypatch.setattr(tv_downloads.tv_store, 'connect', lambda: Conn())
    monkeypatch.setattr(tv_downloads.tv_settings, 'libraries', lambda: [{'id': 'p', 'path': str(tmp_path)}])
    hit = tv_downloads._series_for('Blade.Runner.2099.S01.1080p.WEB-DL-FLUX')
    assert hit and hit['id'] == 1
    assert tv_downloads._series_for('Blade.Runner.2049.S01.1080p.WEB-DL-FLUX') is None
    _ = json
