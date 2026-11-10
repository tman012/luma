/** The page routes of a LUMA_MINIMAL build. MinimalApp (App.tsx) renders exactly these and sends every
 * other path Home, and the server (app/minimal.py) serves the app page only for them. Navigation that has
 * to land on a real page asks minimalRouteExists() instead of keeping its own list. */
import { matchPath } from 'react-router';

export const MINIMAL_ROUTES = {
  home: '/',
  login: '/login',
  start: '/start',
  unlock: '/unlock',
  settings: '/settings',
  settingsLibraries: '/settings/libraries',
  settingsSection: '/settings/:section',
  settingsSub: '/settings/:section/*',
} as const;

/** True when the minimal app has a page for this href (query and hash ignored). */
export function minimalRouteExists(href: string): boolean {
  const path = href.split(/[?#]/)[0] || '/';
  return Object.values(MINIMAL_ROUTES).some(pattern => matchPath(pattern, path) !== null);
}
