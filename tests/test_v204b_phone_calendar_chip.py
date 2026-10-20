"""v204 (Taylor's phone picks on the home 'TV calendar' sheet): Luma's own TV calendar data (not Sonarr),
day-nav A, the Q2a quality chip, T2 air-time headers in ET, a tap opens the E2 episode page; and the movie
modal's Add to watchlist is prod 1b4ea74's white pill."""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHEET = (ROOT / 'frontend/src/components/calendar/TvCalendarSheet.tsx').read_text()
CAL = (ROOT / 'frontend/src/components/library/TvCalendar.tsx').read_text()


def test_sheet_reads_luma_calendar_not_sonarr():
    assert '/api/sonarr/calendar' not in SHEET and 'sonarr' not in SHEET.lower().replace('sonarr is not read', '')
    # TvCalendar loads /api/tv/calendar (loadCalendar) and is forced to the phone layout: day-nav A + Q2a chip
    assert '<TvCalendar today={today} week={week} onWeek={setWeek} toolbar={false} phone />' in SHEET
    assert 'const phone = forcePhone || isPhone;' in CAL
    assert '<DayHeader day={day} today={today} onStep={step} />' in CAL
    assert CAL.count('<QualityChip e={e}') >= 2 and "if (!e.downloaded || !e.quality) return null;" in CAL


def test_tap_opens_e2_episode_page():
    assert '<OpenShowContext.Provider value={openEpisode}>' in SHEET
    assert 'if (episodeId) setEp({ seriesId, episodeId });' in SHEET
    assert '<TvEpisodePage key={`${ep.seriesId}/${ep.episodeId}`} seriesId={ep.seriesId} episodeId={ep.episodeId}' in SHEET
    assert 'onBack={() => setEp(null)}' in SHEET
    # the calendar row calls the context with the episode id
    assert 'const go = () => openShow(e.series_id, e.episode_id);' in CAL


def test_t2_time_headers_in_et():
    assert "timeZone: 'America/New_York'" in CAL and 'airGroups(list).map(' in CAL and 'data-testid="tv-time-head"' in CAL


@pytest.mark.skipif(not shutil.which('node'), reason='node not installed')
def test_et_grouping_behaviour(tmp_path):
    start = CAL.index('/** \'8:00 PM\' in ET')
    code = CAL[start:CAL.index('return out;\n}', start) + len('return out;\n}')]
    js = (code.replace('export function', 'function').replace(': CalEpisode[]', '').replace('[string, CalEpisode[]][]', '')
          .replace('(airstamp: string | null | undefined): string', '(airstamp)').replace('(list): ', '(list) ')
          .replace('const out: = []', 'const out = []').replace('const untimed = []', 'const untimed = []'))
    import re
    js = re.sub(r'const (\w+): [^=]+=', r'const \1 =', js)
    js = re.sub(r'\)\s*:\s*\{', ') {', js)
    js += """
const eps = [{id:1, airstamp:'2026-10-19T00:00:00Z'}, {id:2, airstamp:'2026-10-19T01:00:00Z'},
             {id:3, airstamp:'2026-10-19T01:00:00Z'}, {id:4, airstamp:null}];
console.log(JSON.stringify(airGroups(eps).map(([t, g]) => [t, g.map(e => e.id)])));
"""
    f = tmp_path / 't.js'
    f.write_text(js)
    out = subprocess.run(['node', str(f)], capture_output=True, text=True, check=True).stdout.strip()
    assert out.replace('\u202f', ' ') == '[["8:00 PM",[1]],["9:00 PM",[2,3]],["",[4]]]'




def _prod(path):
    return subprocess.run(['git', 'show', f'1b4ea74:{path}'], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def _norm(s):   # v205 (PM): prod's capitalization exactly, except the confirm sheet (not part of the ask)
    return s.replace('m Watchlist', 'm watchlist')


def test_compact_watchlist_buttons_and_confirm_are_prods():
    f = 'frontend/src/components/media/MovieDetailModal/CompactLayout.tsx'
    prod, test = _prod(f), (ROOT / f).read_text()
    def block(s, a, b):
        i = s.index(a)
        return s[i:s.index(b, i)]
    import re
    strip = lambda s: re.sub(r'\s+data-(testid|modal-keep)="[^"]*"|\s*/\*.*?\*/|\s+onClick=\{[^\n]*\n', '', s, flags=re.S)
    p_in = block(prod, "borderRadius: '50px',\n                          background: 'rgba(74,222,128,0.12)'", 'In Watchlist')
    assert _norm(p_in) in test
    p_add = block(prod, "width: 'calc(100% - 32px)',\n                        margin: '20px 16px 0',\n                        padding: '14px 20px',\n                        background: '#ffffff',\n                        color: '#000000',", '}}')
    assert test.count(p_add) >= 2            # Watch and Add to watchlist are prod's same white pill
    p_conf = block(prod, '{showRemoveConfirm && (', "'Remove from Watchlist'}")
    assert _norm(p_conf) in test
    assert "removeFromWatchlist(id)" in test and 'onClick={handleUpgradeToggle}' in test   # prod's picker step


def test_native_watchlist_remove_api():
    s = (ROOT / 'frontend/src/services/watchlistApi.ts').read_text()
    i = s.index('export async function removeFromWatchlist')
    assert "if (isMinimal())" in s[i:i + 300] and "method: 'DELETE'" in s[i:i + 400]


def test_expanded_watchlist_pill_is_prods():
    f = 'frontend/src/components/media/MovieDetailModal/ExpandedLayout.tsx'
    test = (ROOT / f).read_text()
    assert "label={inWatchlist ? 'In Watchlist' : 'Watchlist'}" in test
    i = test.index("label={inWatchlist ? 'In Watchlist' : 'Watchlist'}")
    assert 'primary\n' in test[i - 1500:i] and "tone={inWatchlist ? undefined : 'blue'}" not in test


def test_picker_sheet_is_prods_and_native_on_test():
    f = 'frontend/src/components/media/MovieDetailModal/MobileUpgradeSheet.tsx'
    s = (ROOT / f).read_text()
    assert "apiFetch(isMinimal() ? '/api/movies/watchlist/profiles' : '/api/radarr/upgrade-profiles')" in s
    assert 'addToNativeWatchlist({ tmdbId, title, year, profileId: opt?.id ?? null })' in s
    prod = _prod(f)
    ui = prod[prod.index('{/* Title */}'):]   # v205: prod's 'Select Quality' exactly
    assert ui in s                                    # prod's picker UI, unchanged


def test_settings_movies_picker_option():
    s = (ROOT / 'frontend/src/components/movies/MoviesSettings.tsx').read_text()
    assert '>Watchlist picker</h3>' in s and 'put({ picker_profiles: all }' in s


def test_v205_prod_capitalization_and_guard_exemption():
    m = ROOT / 'frontend/src/components/media/MovieDetailModal'
    c = (m / 'CompactLayout.tsx').read_text()
    assert 'Add to Watchlist\n' in c and 'In Watchlist\n' in c
    assert "'Select Quality'" in (m / 'MobileUpgradeSheet.tsx').read_text()
    g = (ROOT / 'frontend/tests/sentenceCaseGuard.test.ts').read_text()
    for lbl in ('Add to Watchlist', 'In Watchlist', 'Select Quality', 'Watchlist'):
        assert f"{lbl}$|" in g
