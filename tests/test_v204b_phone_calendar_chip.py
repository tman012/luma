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
    # v325 (Taylor): phone rows (U3Row) drop the Q2a chip; the green/red left edge is the only got-it indicator
    u3 = CAL[CAL.index('function U3Row('):]
    assert '<QualityChip' not in u3 and '<Stripe color={STATE_COLOR[st]} w={3} />' in u3


def test_tap_opens_e2_episode_page():
    assert '<OpenShowContext.Provider value={openEpisode}>' in SHEET
    assert 'if (episodeId) setEp({ seriesId, episodeId });' in SHEET
    assert '<TvEpisodePage key={`${ep.seriesId}/${ep.episodeId}`} seriesId={ep.seriesId} episodeId={ep.episodeId}' in SHEET
    assert 'onBack={() => setEp(null)}' in SHEET
    # the calendar row calls the context with the episode id
    assert 'const go = () => openShow(e.series_id, e.episode_id);' in CAL


def test_u3_replaces_t2_headers():
    """v211 (Taylor's pick U3): no air-time group headers; a leading ET time column, vertically centered."""
    assert 'airGroups' not in CAL and 'tv-time-head' not in CAL and 'tv-time-group' not in CAL
    assert "timeZone: 'America/New_York'" in CAL and '<U3Row key={e.episode_id}' in CAL
    assert "alignItems: 'center', justifyContent: 'center', alignSelf: 'center'" in CAL




def _prod(path):
    return subprocess.run(['git', 'show', f'1b4ea74:{path}'], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def _norm(s):   # v205 (PM): prod's capitalization exactly, except the confirm sheet (not part of the ask)
    return s   # v206: prod exactly, confirm sheet included


def test_compact_watchlist_buttons_and_confirm_are_prods():
    f = 'frontend/src/components/media/MovieDetailModal/CompactLayout.tsx'
    prod, test = _prod(f), (ROOT / f).read_text()
    def block(s, a, b):
        i = s.index(a)
        return s[i:s.index(b, i)]
    import re
    # v266: confirm dialog may carry className="luma-fullscreen luma-bleed" (or luma-bleed alone)
    strip = lambda s: re.sub(r'\s+data-(testid|modal-keep)="[^"]*"|\s+className="(?:luma-fullscreen(?: luma-bleed)?|luma-bleed)"|\s*/\*.*?\*/|\s+onClick=\{[^\n]*\n', '', s, flags=re.S)
    p_in = block(prod, "borderRadius: '50px',\n                          background: 'rgba(74,222,128,0.12)'", 'In Watchlist')
    assert _norm(p_in) in test
    p_add = block(prod, "width: 'calc(100% - 32px)',\n                        margin: '20px 16px 0',\n                        padding: '14px 20px',\n                        background: '#ffffff',\n                        color: '#000000',", '}}')
    assert test.count(p_add) >= 2            # Watch and Add to watchlist are prod's same white pill
    p_conf = block(prod, '{showRemoveConfirm && (', "'Remove from Watchlist'}")
    # v264: the confirm dialog carries luma-bleed (full-bleed on phone); strip it for the prod match
    assert strip(_norm(p_conf)) in strip(test)
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


def test_v211_tv_calendar_sheet_runs_to_the_bottom_edge():
    """Designer: like v210's Settings sheet, the backdrop fills the screen and the inset is
    only inside the scroll content (no band below the sheet). v266: .luma-fullscreen / --luma-screen-h."""
    assert 'className="luma-fullscreen"' in SHEET
    assert "bottom: 'calc(-1 * var(--luma-bleed))'" not in SHEET
    assert "position: 'fixed', top: 0, left: 0, right: 0," in SHEET
    i = SHEET.index('data-scroll-container')
    assert SHEET.index("var(--luma-end-pad) + 12px", i) > i
