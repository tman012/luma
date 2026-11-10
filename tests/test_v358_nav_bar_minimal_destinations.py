"""v358 desktop nav bar on LUMA_MINIMAL: every destination the bar offers is a page the minimal server
serves, and the pages minimal mode has no screen for stay closed (no allowance was added for the bar)."""
import re
from pathlib import Path

from app import minimal

SRC = Path(__file__).resolve().parent.parent / 'frontend' / 'src'
DOCK = (SRC / 'components' / 'home' / 'DesktopDock.tsx').read_text()
ROUTES = (SRC / 'minimalRoutes.ts').read_text()
APP = (SRC / 'App.tsx').read_text()

DEST_RE = re.compile(r"\{ id: '(\w+)', name: '(\w+)', href: '([^']+)'(?:, minimalHref: '([^']+)')? \}")


def _destinations():
    table = DOCK.split('export const DESKTOP_DESTINATIONS', 1)[1].split('];', 1)[0]
    return [m.groups() for m in DEST_RE.finditer(table)]


def _minimal_patterns():
    table = ROUTES.split('export const MINIMAL_ROUTES = {', 1)[1].split('}', 1)[0]
    return re.findall(r":\s*'([^']+)'", table)


def _in_minimal_router(href: str) -> bool:
    path = re.split(r'[?#]', href)[0]
    for pattern in _minimal_patterns():
        rx = '^' + re.escape(pattern).replace(r'/\*', '(?:/.*)?').replace(r':section', '[^/]+') + '/?$'
        if re.match(rx, path):
            return True
    return False


def test_the_bar_lists_the_four_destinations_in_order():
    assert [(i, n, h) for i, n, h, _ in _destinations()] == [
        ('libraries', 'Libraries', '/browse'), ('explore', 'Explore', '/explore'),
        ('calendar', 'Calendar', '/settings/tv'), ('watchlist', 'Watchlist', '/watchlist')]


def test_minimal_destinations_are_pages_the_minimal_server_and_router_serve():
    offered = [(name, mhref or href) for _, name, href, mhref in _destinations() if _in_minimal_router(mhref or href)]
    assert offered == [('Libraries', '/settings/tv?view=series'), ('Calendar', '/settings/tv')]
    for _, href in offered:
        assert minimal.path_allowed(re.split(r'[?#]', href)[0], 'GET')


def test_minimal_router_is_the_one_route_table():
    assert _minimal_patterns() == ['/', '/login', '/start', '/unlock', '/settings', '/settings/libraries',
                                   '/settings/:section', '/settings/:section/*']
    block = APP.split('function MinimalApp', 1)[1].split('export default function App', 1)[0]
    assert re.findall(r'<Route path=(\{[^}]+\}|"[^"]+")', block) == [
        '{MINIMAL_ROUTES.home}', '{MINIMAL_ROUTES.login}', '{MINIMAL_ROUTES.start}', '{MINIMAL_ROUTES.unlock}',
        '{MINIMAL_ROUTES.settings}', '{MINIMAL_ROUTES.settingsLibraries}', '{MINIMAL_ROUTES.settingsSection}',
        '{MINIMAL_ROUTES.settingsSub}', '"*"']
    # every page route in that table is one the server serves in minimal mode
    for pattern in _minimal_patterns():
        assert minimal.path_allowed(pattern.replace(':section', 'tv').replace('/*', '/settings'), 'GET')


def test_pages_without_a_minimal_screen_stay_closed():
    # The full app's Libraries, Explore and Watchlist pages and the data they read are outside the minimal
    # core. The bar does not offer them there; nothing was opened up to make a button work.
    for path in ('/browse', '/explore', '/watchlist', '/library',
                 '/api/library/browse', '/api/library/genres', '/api/watchlist', '/api/watchlist/tv',
                 '/api/trakt/movies/trending', '/api/explore/cached', '/api/in-theaters', '/api/sonarr/calendar'):
        assert not minimal.path_allowed(path, 'GET'), path
        assert not _in_minimal_router(path), path


def test_no_silent_no_op_navigation_left_in_the_bar():
    assert 'desktopDestNavigates' not in DOCK
    assert "startsWith('/settings')) return" not in DOCK
    assert 'minimalRouteExists(href)' in DOCK
