"""v185.1 (Taylor, PM final): a new show's series folder is the plain Sonarr '{Series Title}' (TVDB-style
tags kept), gets ' (Year)' only when another show has that name, and reuses the folder the library
already has for the show as spelled; the naming migration (FF2)."""
import os

from app import tv_downloads, tv_naming, tv_settings, tv_store
from tests.test_file_management import HDR, minimal as minimal_app, store  # noqa: F401
from tests.test_tv_calendar import aired, scanned  # noqa: F401
from tests.test_tv_download_watch_v158a import H1, dl, grab  # noqa: F401
from tests.test_tv_libraries import owner, tv_env  # noqa: F401
from tests.test_tv_scan import PID, lib, rows, select  # noqa: F401
from tests.tv_fake import fake_tmdb  # noqa: F401
from tests.test_v185_naming import tvs_store  # noqa: F401


def test_existing_folder_matches_the_show_with_its_year_country_or_bare():
    t = {'title': 'Time', 'year': 2021}
    assert tv_naming.existing_folder(t, ['Time (2021)', 'Time (1999)', 'Timeless']) == 'Time (2021)'
    assert tv_naming.existing_folder(t, ['Time (1999)']) is None                        # another year: another show
    assert tv_naming.existing_folder({'title': 'Your Honor', 'year': 2020}, ['Your Honor (US)']) == 'Your Honor (US)'
    assert tv_naming.existing_folder({'title': 'Firefly', 'year': 2002}, ['Firefly']) == 'Firefly'
    assert tv_naming.existing_folder({'title': 'Meltdown: Three Mile Island', 'year': 2022}, ['Meltdown- Three Mile Island']) == 'Meltdown- Three Mile Island'
    assert tv_naming.existing_folder({'title': 'Archer', 'year': 2009}, ['Archer (2009)', 'Archer']) == 'Archer (2009)'


def test_a_new_show_folder_is_the_plain_title(tvs_store):  # noqa: F811
    assert tv_settings.series_folder('Archer', 2009) == 'Archer'
    assert tv_settings.series_folder('The Office (US)', 2005) == 'The Office (US)'


def _unfolder(sid=None):
    with tv_store.write() as conn:
        conn.execute("UPDATE series SET folder = NULL WHERE title = 'Severance'")
        return conn.execute("SELECT id FROM series WHERE title = 'Severance'").fetchone()['id']


def test_an_import_reuses_the_library_s_folder_for_the_show(dl, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_downloads, '_naming', lambda: {**tv_naming.DEFAULTS, 'rename_episodes': False})
    os.rename(dl.lib / 'Severance (2022)', dl.lib / 'Severance (US)')
    sid = _unfolder()
    rel = 'Severance.S02E03.1080p.WEB-DL-GRP'
    grab(dl, H1, rel, [rel + '.mkv'])
    assert dl.tick()['imported'] == 1
    assert (dl.lib / 'Severance (US)' / 'Season 02' / (rel + '.mkv')).is_file()
    assert not (dl.lib / 'Severance (2022)').exists()
    assert rows('SELECT folder FROM series WHERE id = ?', sid)[0]['folder'] == 'Severance (US)'


def test_with_no_folder_there_the_new_one_is_the_plain_title(dl, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_downloads, '_naming', lambda: {**tv_naming.DEFAULTS, 'rename_episodes': False})
    os.rename(dl.lib / 'Severance (2022)', dl.lib / 'Severance (2019)')               # another year: not this show
    sid = _unfolder()
    rel = 'Severance.S02E03.1080p.WEB-DL-GRP'
    grab(dl, H1, rel, [rel + '.mkv'])
    assert dl.tick()['imported'] == 1
    assert (dl.lib / 'Severance' / 'Season 2' / (rel + '.mkv')).is_file()
    assert rows('SELECT folder FROM series WHERE id = ?', sid)[0]['folder'] == 'Severance'


def test_a_suffixed_season_folder_is_reused():
    assert tv_naming.season_folder(8, ['Season 7', 'Season 8 (2018)'], dict(tv_naming.DEFAULTS)) == 'Season 8 (2018)'
    assert tv_naming.season_folder(8, ['Season 08 - Apocalypse'], dict(tv_naming.DEFAULTS)) == 'Season 08 - Apocalypse'
    assert tv_naming.season_folder(8, ['Season 80', 'Season 18 (2030)'], dict(tv_naming.DEFAULTS)) == 'Season 8'


# ---------------------------------------------------------------- FF2: the naming migration

import json  # noqa: E402
import stat  # noqa: E402

OLD = {'series_folder': '{title} ({year})', 'season_folder': 'Season {season:02}',
       'episode': '{series} - S{season:02}E{episode:02} - {episode_title}',
       'daily': '{series} - {air_date} - {episode_title}', 'specials_folder': 'Specials'}


def _store(tmp_path, monkeypatch, body):
    p = tmp_path / 'tvs' / tv_settings.STORE_NAME
    p.parent.mkdir(exist_ok=True)
    monkeypatch.setitem(tv_settings._cfg, 'store_path', str(p))
    p.write_text(json.dumps(body))
    return p


def test_taylors_live_store_the_pre_v185_code_defaults_map_to_the_new_defaults(tmp_path, monkeypatch):
    p = _store(tmp_path, monkeypatch, {'version': 1, 'paths': [], 'naming': dict(OLD)})
    before = p.read_text()
    n = tv_settings.load()['naming']
    assert n['series_folder'] == '{Series Title}' and tv_settings.series_folder('Time', 2021) == 'Time'
    assert n['season_folder'] == 'Season {season}' and tv_naming.season_folder(8, [], n) == 'Season 8'
    assert n['standard'] == tv_naming.DEFAULTS['standard'] and n['daily'] == tv_naming.DEFAULTS['daily']
    assert n['specials_folder'] == 'Specials'
    bak = p.with_name(p.name + tv_settings.BACKUP_SUFFIX)
    assert bak.read_text() == before and stat.S_IMODE(bak.stat().st_mode) == 0o600
    saved = json.loads(p.read_text())
    assert saved['naming_rev'] == tv_settings.NAMING_REV and saved['naming']['series_folder'] == '{Series Title}'
    assert 'episode' not in saved['naming'] and 'naming_legacy' not in saved
    tv_settings.load()                                                  # once: the backup is never overwritten
    assert bak.read_text() == before


def test_custom_old_values_are_converted_token_by_token(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, {'naming': {'series_folder': '{title} [{year}]', 'season_folder': 'S{season:02}',
                                              'episode': '{series} {season}x{episode:02} {episode_title} {resolution}'}})
    n = tv_settings.load()['naming']
    assert n['series_folder'] == '{Series Title} [{Series Year}]'
    assert n['season_folder'] == 'S{season:00}'
    assert n['standard'] == '{Series Title} {season}x{episode:00} {Episode Title} {Quality Title}'


def test_an_unconvertible_value_is_kept_not_discarded(tmp_path, monkeypatch):
    p = _store(tmp_path, monkeypatch, {'naming': {'series_folder': '{title} [tmdb-{tmdb_id}]'}})
    d = tv_settings.load()
    assert d['naming']['series_folder'] == '{Series Title}'
    assert json.loads(p.read_text())['naming_legacy'] == {'series_folder': '{title} [tmdb-{tmdb_id}]'}
    tv_settings.save(tv_settings.load())
    assert json.loads(p.read_text())['naming_legacy'] == {'series_folder': '{title} [tmdb-{tmdb_id}]'}


def test_a_v185_store_keeps_its_sonarr_values_and_gets_the_backup(tmp_path, monkeypatch):
    v185 = dict(tv_naming.DEFAULTS, series_folder='{Series Title}', season_folder='Season {season}')
    p = _store(tmp_path, monkeypatch, {'version': 1, 'naming': v185})
    assert tv_settings.load()['naming']['series_folder'] == '{Series Title}'
    assert p.with_name(p.name + tv_settings.BACKUP_SUFFIX).exists()


def test_a_v185_custom_sonarr_format_is_kept(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, {'naming': {'series_folder': '{Series Title} ({Series Year}) [x]', 'season_folder': 'Season {season:00}'}})
    n = tv_settings.load()['naming']
    assert (n['series_folder'], n['season_folder']) == ('{Series Title} ({Series Year}) [x]', 'Season {season:00}')


def test_a_fresh_install_writes_no_backup(tmp_path, monkeypatch):
    p = tmp_path / 'tvs' / tv_settings.STORE_NAME
    monkeypatch.setitem(tv_settings._cfg, 'store_path', str(p))
    tv_settings.save(tv_settings.load())
    tv_settings.load()
    assert not p.with_name(p.name + tv_settings.BACKUP_SUFFIX).exists()


TAYLOR_STALE = {'series_folder': '{title} ({year})', 'season_folder': 'Season {season:02}',
                'episode': '{series} - S{season:02}E{episode:02} - {episode_title}', 'specials_folder': 'Specials'}


def test_pm_taylors_stale_settings_give_season_8_for_ahs_and_padded_siblings_even_after_an_options_save(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, {'version': 1, 'paths': [], 'naming': dict(TAYLOR_STALE)})
    ahs = [f'Season {n}' for n in range(1, 8)]                  # AHS-like: unpadded, no Season 8 yet
    padded = [f'Season {n:02d}' for n in range(1, 8)]
    for round_trip in (False, True):
        d = tv_settings.load()
        if round_trip:                                          # Options: GET the naming, PUT it back unchanged
            assert tv_settings.apply_put(d, {'naming': dict(d['naming'])}) == {}
            tv_settings.save(d)
            d = tv_settings.load()
        n = d['naming']
        assert n['season_folder'] == 'Season {season}'           # the stale code default is never carried over
        assert n['series_folder'] == '{Series Title}'
        assert tv_naming.season_folder(8, ahs, n) == 'Season 8'
        assert tv_naming.season_folder(8, padded, n) == 'Season 8'
        assert tv_naming.season_folder(8, ahs + ['Season 08'], n) == 'Season 08'   # an existing one is reused


# ---------------------------------------------------------------- same-name shows (Taylor, PM)

def test_the_office_us_and_uk_keep_their_tags_and_never_take_each_others_folder():
    us, uk = {'title': 'The Office (US)', 'year': 2005}, {'title': 'The Office (UK)', 'year': 2001}
    names = ['The Office (US)', 'The Office (UK)', 'The Office']
    assert tv_naming.existing_folder(us, names) == 'The Office (US)'
    assert tv_naming.existing_folder(uk, names) == 'The Office (UK)'
    assert tv_naming.existing_folder(uk, ['The Office (US)', 'The Office']) is None   # never the US one, nor bare
    assert tv_naming.existing_folder({'title': 'Doctor Who (2005)', 'year': 2005}, ['Doctor Who (1963)', 'Doctor Who (2005)']) == 'Doctor Who (2005)'
    c = dict(tv_naming.DEFAULTS)
    assert tv_naming.series_folder({'title': 'Doctor Who (2005)', 'year': 2005}, c) == 'Doctor Who (2005)'   # no '(2005) (2005)'
    assert tv_naming.series_folder({'title': 'The Office (US)', 'year': 2005}, dict(c, series_folder='{Series Title}')) == 'The Office (US)'
    assert tv_naming.render('{Series Title}', {'title': 'The Office (US)'}, c) == 'The Office (US)'   # never stripped


def _second_severance(dl, folder_of_first):
    """Another show (another tvdb id) already in tv.db that owns *folder_of_first*. (Its title differs so the
    release still names one show; two same-titled shows make the release ambiguous, refused upstream.)"""
    with tv_store.write() as conn:
        conn.execute('UPDATE series SET tvdb_id = 1 WHERE title = ?', ('Severance',))
        conn.execute("INSERT INTO series (tmdb_id, tvdb_id, title, year, path_id, folder, monitored, added_at)"
                     " VALUES (990777, 2, 'Severance Documentary', 2019, ?, ?, 0, 0)", (PID, folder_of_first))


def test_a_collision_with_another_show_and_no_tag_gets_the_year(dl, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_downloads, '_naming', lambda: {**tv_naming.DEFAULTS, 'rename_episodes': False,
                                                          'series_folder': '{Series Title}'})
    os.rename(dl.lib / 'Severance (2022)', dl.lib / 'Severance')
    sid = _unfolder()
    _second_severance(dl, 'Severance')                   # the bare folder is the OTHER show's
    rel = 'Severance.S02E03.1080p.WEB-DL-GRP'
    grab(dl, H1, rel, [rel + '.mkv'])
    assert dl.tick()['imported'] == 1
    assert (dl.lib / 'Severance (2022)' / 'Season 2' / (rel + '.mkv')).is_file()
    assert rows('SELECT folder FROM series WHERE id = ?', sid)[0]['folder'] == 'Severance (2022)'
    assert not list((dl.lib / 'Severance').rglob(rel + '*'))          # nothing went into the other show's folder


def test_an_existing_title_year_folder_is_reused_as_spelled(dl, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tv_downloads, '_naming', lambda: {**tv_naming.DEFAULTS, 'rename_episodes': False,
                                                          'series_folder': '{Series Title}'})
    sid = _unfolder()                                    # 'Severance (2022)' is on disk, not owned in tv.db
    _second_severance(dl, 'Severance')
    rel = 'Severance.S02E03.1080p.WEB-DL-GRP'
    grab(dl, H1, rel, [rel + '.mkv'])
    assert dl.tick()['imported'] == 1
    assert rows('SELECT folder FROM series WHERE id = ?', sid)[0]['folder'] == 'Severance (2022)'
