"""v185 (Taylor): Sonarr-style episode naming on import, seeded from his Sonarr NamingConfig."""
import os

import pytest

from app import tv_downloads, tv_naming, tv_settings, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_download_watch_v158a import H1, H2, dl, grab  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401

D = tv_naming.DEFAULTS


def test_the_defaults_are_taylors_sonarr_naming_config():
    assert D['standard'] == '{Series Title} - S{season:00}E{episode:00} - {Episode Title}'
    assert D['daily'] == '{Series Title} - {Air-Date} - {Episode Title} {Quality Full}'
    assert D['anime'] == '{Series Title} - S{season:00}E{episode:00} - {Episode Title} {Quality Full}'
    assert (D['series_folder'], D['season_folder'], D['specials_folder']) == ('{Series Title}', 'Season {season}', 'Specials')
    assert D['multi_episode_style'] == 'prefixed_range' and D['rename_episodes'] and D['replace_illegal']
    assert D['colon_replacement'] == 'dash'          # what the library shows, not the Smart enum


def test_the_library_samples_come_out_the_same():
    std = {'title': 'Meltdown: Three Mile Island', 'season': 1, 'episodes': [1], 'titles': ['The Accident']}
    assert tv_naming.episode_name(std, '.mkv', 'standard', D) == 'Meltdown- Three Mile Island - S01E01 - The Accident.mkv'
    daily = {'title': 'Dateline NBC', 'season': 34, 'episodes': [1], 'titles': ['The Phantom'], 'air_date': '2025-09-26',
             'quality': '1080p-web'}
    assert tv_naming.episode_name(daily, '.mkv', 'daily', D) == 'Dateline NBC - 2025-09-26 - The Phantom WEBDL-1080p.mkv'
    archer = {'title': 'Archer', 'folder': 'Archer (2009)', 'season': 1, 'episodes': [1], 'titles': ['Mole Hunt']}
    assert tv_naming.episode_name(archer, '.mkv', 'standard', D) == 'Archer (2009) - S01E01 - Mole Hunt.mkv'
    assert tv_naming.series_folder({'title': 'Meltdown: Three Mile Island'}, D) == 'Meltdown- Three Mile Island'


def test_the_extension_is_kept_and_illegal_characters_replaced():
    c = {'title': 'What If...?', 'season': 1, 'episodes': [2], 'titles': ['A/B: "C" *D* <E>|']}
    assert tv_naming.episode_name(c, '.MP4', 'standard', D) == 'What If...! - S01E02 - A+B- C -D- E.MP4'
    assert tv_naming.episode_name(c, '.mkv', 'standard', {**D, 'replace_illegal': False}) == 'What If... - S01E02 - AB- C D E.mkv'


@pytest.mark.parametrize('style,want', [
    ('extend', 'S01E01-02-03'), ('duplicate', 'S01E01 - S01E02 - S01E03'), ('repeat', 'S01E01E02E03'),
    ('scene', 'S01E01-E02-E03'), ('range', 'S01E01-03'), ('prefixed_range', 'S01E01-E03')])
def test_multi_episode_styles(style, want):
    c = {'title': 'The IT Crowd', 'season': 1, 'episodes': [1, 2, 3], 'titles': ['A', 'B', 'C']}
    assert tv_naming.episode_name(c, '.mkv', 'standard', {**D, 'multi_episode_style': style}) == f'The IT Crowd - {want} - A + B + C.mkv'
    two = {**c, 'episodes': [1, 2], 'titles': ['Pilot (1)', 'Pilot (2)']}
    assert tv_naming.episode_name(two, '.mkv', 'standard', D) == 'The IT Crowd - S01E01-E02 - Pilot.mkv'


@pytest.mark.parametrize('mode,want', [('delete', 'A B'), ('dash', 'A- B'), ('space_dash', 'A - B'),
                                       ('space_dash_space', 'A - B'), ('smart', 'A - B')])
def test_colon_replacement(mode, want):
    assert tv_naming.colon('A: B', mode) == want
    assert tv_naming.colon('10:30', 'smart') == '10-30'


def test_tokens_case_separator_and_empty_values():
    c = {'title': 'The Bear', 'season': 1, 'episodes': [2], 'titles': ['Hands'], 'quality': None}
    assert tv_naming.render('{series.title}.S{season:00}E{episode:00}', c, D) == 'the.bear.S01E02'
    assert tv_naming.render('{SERIES TITLE} - {Episode Title} {Quality Full}', c, D) == 'THE BEAR - Hands'
    assert tv_naming.render('{Series Title} - S{season:00}E{episode:00} - {Episode Title}', {**c, 'titles': ['']}, D) == 'The Bear - S01E02'
    assert tv_naming.render('{Series TitleYear}', {**c, 'year': 2022}, D) == 'The Bear (2022)'


def test_season_folders_reuse_the_show_s_own_or_its_padding_style():
    assert tv_naming.season_folder(3, ['Season 03', 'Season 01'], D) == 'Season 03'            # its own folder
    assert tv_naming.season_folder(3, ['season 3'], D) == 'season 3'
    assert tv_naming.season_folder(4, ['Season 01', 'Season 02'], D) == 'Season 4'             # hotfix: the format, not siblings
    assert tv_naming.season_folder(4, ['Season 1', 'Season 2'], D) == 'Season 4'
    assert tv_naming.season_folder(4, ['Extras'], D) == 'Season 4'                             # the format
    assert tv_naming.season_folder(4, [], {**D, 'season_folder': 'S{season:00}'}) == 'S04'
    assert tv_naming.season_folder(0, ['Season 00', 'Season 01'], D) == 'Season 00'
    assert tv_naming.season_folder(0, ['Season 01'], D) == 'Specials'


@pytest.mark.parametrize('key,value', [('standard', '{Series Title} - {Bogus}'), ('standard', '{Series Title}'),
                                       ('daily', '{Series Title} - {Episode Title}'), ('anime', '{Series Title}'),
                                       ('season_folder', 'Season'), ('series_folder', '{Series Title}/x'),
                                       ('standard', '{Series Title} {season:xx}{episode}'), ('standard', '')])
def test_bad_formats_are_refused(key, value):
    assert tv_naming.check_format(key, value) is not None


def test_settings_store_and_preview(tvs_store):
    d = tv_settings.load()
    assert tv_settings.apply_put(d, {'naming': {'standard': '{Series Title} S{season:00}E{episode:00}', 'colon_replacement': 'smart'}}) == {}
    p = tv_naming.preview(d['naming'])
    assert p['standard'] == 'Meltdown - Three Mile Island S01E01.mkv'
    assert p['series_folder'] == 'Meltdown - Three Mile Island' and p['season_folder'] == 'Season 1' and p['specials_folder'] == 'Specials'
    assert p['multi_episode_style'] == 'The IT Crowd S01E01-E03.mkv'
    errs = tv_settings.apply_put(d, {'naming': {'standard': '{Series Title}', 'multi_episode_style': 'x'}})
    assert set(errs) == {'naming.standard', 'naming.multi_episode_style'}
    # an earlier build's templates start from Taylor's Sonarr formats
    assert tv_naming.normalize({'series_folder': '{title} ({year})', 'episode': 'x'}) == D


@pytest.fixture
def tvs_store(tmp_path, monkeypatch):
    monkeypatch.setitem(tv_settings._cfg, 'store_path', str(tmp_path / 'tvs' / tv_settings.STORE_NAME))


def test_preview_route_is_the_owners(minimal_app, tvs_store, owner):  # noqa: F811
    from tests.test_home_rails import _client
    anon = _client(minimal_app)
    body = {'naming': {'standard': '{Series Title} - {season:00}x{episode:00}'}}
    r = anon.post('/api/settings/tv/naming/preview', json=body, headers=HDR)
    assert r.status_code in (401, 403)
    r = owner.post('/api/settings/tv/naming/preview', json=body, headers=HDR)
    assert r.status_code == 200 and r.get_json()['examples']['standard'] == 'Meltdown- Three Mile Island - 01x01.mkv'
    r = owner.post('/api/settings/tv/naming/preview', json={'naming': {'standard': '{Nope}'}}, headers=HDR)
    assert r.status_code == 200 and r.get_json()['errors'] == {'naming.standard': 'Unknown token {Nope}'}
    assert r.get_json()['examples']['standard'] == ''


# ---------------------------------------------------------------- on import

def _title(season, ep):
    with tv_store.connect() as conn:
        return conn.execute("SELECT e.title FROM episodes e JOIN series s ON s.id = e.series_id WHERE s.title = 'Severance'"
                            ' AND season_number = ? AND episode_number = ?', (season, ep)).fetchone()['title']


def test_an_import_gets_the_sonarr_name_with_its_sidecars(dl):  # noqa: F811
    rel = 'Severance.S02E03.1080p.WEB-DL.DDP5.1-GRP'
    d = dl.root / rel
    grab(dl, H1, rel, [rel + '.mkv'])
    (d / (rel + '.en.srt')).write_text('1\n')
    (d / (rel + '.en.forced.srt')).write_text('1\n')
    (d / (rel + '.nfo')).write_text('junk')
    assert dl.tick() == {'checked': 1, 'imported': 1}
    season = dl.lib / 'Severance (2022)' / 'Season 02'                 # the show's own folder, reused
    stem = f'Severance (2022) - S02E03 - {tv_naming.clean_text(_title(2, 3), D)}'
    assert (season / f'{stem}.mkv').is_file()
    assert (season / f'{stem}.en.srt').is_file() and (season / f'{stem}.en.forced.srt').is_file()
    assert not any(n.endswith(('.nfo', '.luma-partial')) for n in os.listdir(season))
    assert not (season / (rel + '.mkv')).exists()
    st = tv_downloads.status(H1)
    assert st['state'] == 'imported' and st['files'][0]['linked'] is True and len(st['files'][0]['sidecars']) == 2


def test_never_overwrites_an_existing_name(dl):  # noqa: F811
    season = dl.lib / 'Severance (2022)' / 'Season 02'
    stem = f'Severance (2022) - S02E04 - {tv_naming.clean_text(_title(2, 4), D)}'
    (season / f'{stem}.mkv').write_bytes(b'already-here')
    rel = 'Severance.S02E04.1080p.WEB-DL-GRP'
    grab(dl, H2, rel, [rel + '.mkv'])
    dl.tick()
    assert (season / f'{stem}.mkv').read_bytes() == b'already-here'
    assert tv_downloads.status(H2)['files'][0]['how'] == 'exists'


def test_rename_off_keeps_the_release_name_and_a_new_season_follows_the_siblings(dl, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_downloads, '_naming', lambda: {**D, 'rename_episodes': False})
    with tv_store.write() as conn:
        sid = conn.execute("SELECT id FROM series WHERE title = 'Severance'").fetchone()['id']
        conn.execute("INSERT INTO episodes (series_id, season_number, episode_number, title, air_date, monitored)"
                     " VALUES (?, 3, 1, 'New', '2026-01-01', 1)", (sid,))
    rel = 'Severance.S03E01.1080p.WEB-DL-GRP'
    grab(dl, H1, rel, [rel + '.mkv'])
    assert dl.tick()['imported'] == 1
    assert (dl.lib / 'Severance (2022)' / 'Season 03' / (rel + '.mkv')).is_file()       # 'Season 01/02' padding


def test_a_dotted_format_leaves_no_dash_dot():
    c = {'title': 'Meltdown: Three Mile Island', 'season': 1, 'episodes': [1], 'titles': ['The Accident']}
    assert tv_naming.render('{Series.Title}.S{season:00}E{episode:00}.{Episode.Title}', c, D) == 'Meltdown.Three.Mile.Island.S01E01.The.Accident'
