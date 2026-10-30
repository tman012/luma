/** v327 (Taylor 10:42 spec): Settings > TV is one real settings page (Movies' shell, the Settings sidebar):
 * Library options on top, the old Options body below, no tabs. The TV views (Series | Calendar | Import, from
 * Home > Calendar) have exactly 3 tabs and never the sidebar. Old ?view=libraries|options links redirect.
 * Every TV setting keeps its endpoint and key (moved, not rebuilt). */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const auth = { authenticated: true, loading: false, implicit: false, logout: vi.fn(), user: { id: 1, name: 'taylor', is_admin: true } };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('@/minimal', async orig => ({ ...(await orig<typeof import('@/minimal')>()), isMinimal: () => true }));

import { SettingsPage } from '@/pages/LibrariesPage';
import { TV_SETTINGS_PATH } from '@/components/library/tvSettingsRoutes';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const PATH = { id: 'p_1', name: 'TV', path: '/media/TV', status: 'ok' };
const NAMING = { rename_episodes: true, standard: '{Series Title} - S{season:00}E{episode:00}', daily: 'd', anime: 'a',
  series_folder: '{Series Title}', season_folder: 'Season {season}', specials_folder: 'Specials',
  multi_episode_style: 'extend', colon_replacement: 'smart' };
let tv: Record<string, unknown>;
let sel: string[] = [];
const calls: { url: string; method: string; body: unknown }[] = [];
let loc = '';
function Where() { const l = useLocation(); loc = l.pathname + l.search; return null; }

function setup(wide: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px') ? wide : false, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  }));
}
beforeEach(() => {
  calls.length = 0;
  sel = [PATH.id];
  tv = { paths: [PATH], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6, naming: NAMING, preferred_groups: ['RAWR'],
    automation: { air_searches: true, sweep_enabled: true, on_add: 'search_now' } };
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u);
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url.includes('/api/settings/plex')) return res({ owner: true, configured: true });
    if (url.includes('/api/trackers/types')) return res({ types: [] });
    if (url.includes('/api/settings/sections/tv/trackers')) return res({ section: 'tv', trackers: [] });
    if (url === '/api/settings/tv/libraries') {
      if (method === 'PUT') sel = body.selected;
      return res({ selected: sel, libraries: [{ ...PATH, selected: sel.includes(PATH.id) }] });
    }
    if (url === '/api/settings/tv/library') return res({ shows: [], unmatched: [], scan: { running: false, error: null, last_scan: null } });
    if (url === '/api/settings/tv/profiles') return res({ profiles: [], qualities: [] });
    if (url === '/api/settings/tv/naming/preview') return res({ examples: {}, errors: {} });
    if (url === '/api/settings/tv' && method === 'PUT') {
      if (body.naming) tv = { ...tv, naming: { ...(tv.naming as object), ...body.naming } };
      else if (body.automation) tv = { ...tv, automation: { ...(tv.automation as object), ...body.automation } };
      else tv = { ...tv, ...body };
      return res(tv);
    }
    if (url === '/api/settings/tv') return res(tv);
    if (url.startsWith('/api/settings/tv/calendar')) return res({ episodes: [], start: '2026-10-26', end: '2026-11-01' });
    return res({}, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const at = (path: string) => render(<MemoryRouter initialEntries={[path]}><Routes>
  <Route path="/" element={<div data-testid="home" />} />
  <Route path="/settings" element={<SettingsPage />} />
  <Route path="/settings/:section" element={<SettingsPage />} />
  <Route path="/settings/:section/*" element={<SettingsPage />} />
</Routes><Where /></MemoryRouter>);
const puts = () => calls.filter(c => c.method === 'PUT');

describe('Settings > TV (one page)', () => {
  it('renders Library options then the Options body inside the Settings sidebar shell, no tabs', async () => {
    setup(true);
    at(TV_SETTINGS_PATH);
    const nav = await screen.findByTestId('settings-nav');
    const tvLink = within(nav).getByRole('link', { name: 'TV' });
    expect(tvLink.getAttribute('aria-current')).toBe('page');
    expect(tvLink.getAttribute('href')).toBe(TV_SETTINGS_PATH);
    expect(screen.getByRole('heading', { level: 2, name: 'TV' })).toBeTruthy();
    expect(screen.getByTestId('settings-main').style.maxWidth).toBe('720px');   // Movies' width
    const page = await screen.findByTestId('tv-settings');
    const lib = within(page).getByTestId('tv-library-options');
    const opts = within(page).getByTestId('tv-options');
    expect(lib.compareDocumentPosition(opts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();   // Library options on top
    await within(lib).findAllByTestId('lib-select-row');
    expect(within(lib).getByText('Library options')).toBeTruthy();
    for (const id of ['tv-upgrades', 'tv-profiles', 'tv-preferred-groups', 'tv-search-settings']) expect(within(opts).getByTestId(id)).toBeTruthy();
    await within(opts).findByTestId('tv-naming');
    await within(opts).findByTestId('section-trackers-tv');
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByTestId('tv-tabs')).toBeNull();
    expect(screen.queryByTestId('tv-search')).toBeNull();
  });

  it('every TV setting still loads from and saves to /api/settings/tv* with the same keys', async () => {
    setup(true);
    at(TV_SETTINGS_PATH);
    await screen.findByTestId('tv-settings');
    // loads
    await waitFor(() => {
      const gets = calls.filter(c => c.method === 'GET').map(c => c.url);
      for (const u of ['/api/settings/tv', '/api/settings/tv/libraries', '/api/settings/tv/profiles', '/api/settings/sections/tv/trackers']) expect(gets).toContain(u);
    });
    expect(screen.getByTestId('tv-upgrades-keep_both').getAttribute('aria-checked')).toBe('true');
    expect(screen.getAllByTestId('tv-group').map(g => g.textContent)).toEqual([expect.stringContaining('RAWR')]);
    expect((screen.getByTestId('tv-naming-standard') as HTMLInputElement).value).toBe(NAMING.standard);
    // Library selection: PUT /api/settings/tv/libraries {selected}
    const row = (await screen.findAllByTestId('lib-select-row'))[0]!;
    await act(async () => { fireEvent.click(row); });
    await waitFor(() => expect(puts().find(c => c.url === '/api/settings/tv/libraries')?.body).toEqual({ selected: [] }));
    // Upgrades: PUT /api/settings/tv {upgrades: {mode}}
    await act(async () => { fireEvent.click(screen.getByTestId('tv-upgrades-replace_verified')); });
    await waitFor(() => expect(puts().some(c => c.url === '/api/settings/tv' && JSON.stringify(c.body) === JSON.stringify({ upgrades: { mode: 'replace_verified' } }))).toBe(true));
    // Preferred groups: PUT /api/settings/tv {preferred_groups}
    fireEvent.change(screen.getByLabelText('Group'), { target: { value: 'FLUX' } });
    await act(async () => { fireEvent.click(within(screen.getByTestId('tv-preferred-groups')).getByRole('button', { name: 'Add' })); });
    await waitFor(() => expect(puts().some(c => JSON.stringify(c.body) === JSON.stringify({ preferred_groups: ['RAWR', 'FLUX'] }))).toBe(true));
    // Search: PUT /api/settings/tv {automation: {air_searches | sweep_enabled | on_add}}
    await act(async () => { fireEvent.click(screen.getByTestId('tv-on-add-wait_for_sweep')); });
    await waitFor(() => expect(puts().some(c => JSON.stringify(c.body) === JSON.stringify({ automation: { on_add: 'wait_for_sweep' } }))).toBe(true));
    const search = screen.getByTestId('tv-search-settings');
    await act(async () => { fireEvent.click(within(search).getByRole('switch', { name: 'Air-time searches' })); });
    await waitFor(() => expect(puts().some(c => JSON.stringify(c.body) === JSON.stringify({ automation: { air_searches: false } }))).toBe(true));
    await act(async () => { fireEvent.click(within(search).getByRole('switch', { name: 'Slow sweep for missing episodes' })); });
    await waitFor(() => expect(puts().some(c => JSON.stringify(c.body) === JSON.stringify({ automation: { sweep_enabled: false } }))).toBe(true));
    // Episode naming: PUT /api/settings/tv {naming: {<key>}}
    const std = screen.getByTestId('tv-naming-standard');
    fireEvent.change(std, { target: { value: '{Series Title} S{season:00}E{episode:00}' } });
    await act(async () => { fireEvent.blur(std); });
    await waitFor(() => expect(puts().some(c => JSON.stringify(c.body) === JSON.stringify({ naming: { standard: '{Series Title} S{season:00}E{episode:00}' } }))).toBe(true));
    // saved values reload into the same page
    cleanup();
    at(TV_SETTINGS_PATH);
    await waitFor(() => expect(screen.getByTestId('tv-upgrades-replace_verified').getAttribute('aria-checked')).toBe('true'));
    expect(screen.getByTestId('tv-on-add-wait_for_sweep').getAttribute('aria-checked')).toBe('true');
    expect(screen.getAllByTestId('tv-group')).toHaveLength(2);
  });

  it('phone: Settings > TV is a pushed section with the same two parts, no tabs', async () => {
    setup(false);
    at(TV_SETTINGS_PATH);
    await screen.findByTestId('tv-settings');
    expect(screen.getByTestId('tv-library-options')).toBeTruthy();
    expect(screen.getByTestId('tv-options')).toBeTruthy();
    expect(screen.queryByTestId('tv-tabs')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(loc).toBe('/settings');
  });
});

describe('TV views (Home > Calendar)', () => {
  for (const wide of [true, false]) {
    it(`${wide ? 'desktop' : 'phone'}: exactly Series | Calendar | Import, never the Settings sidebar or the settings sections`, async () => {
      setup(wide);
      for (const p of ['/settings/tv', '/settings/tv?view=series', '/settings/tv?view=import']) {
        at(p);
        const tabs = await screen.findByTestId('tv-tabs');
        expect(within(tabs).getAllByRole('tab').map(t => t.textContent)).toEqual(['Series', 'Calendar', 'Import']);
        expect(screen.queryByTestId('settings-nav')).toBeNull();
        expect(screen.queryByTestId('tv-paths')).toBeNull();
        expect(screen.queryByTestId('tv-upgrades')).toBeNull();
        if (wide) expect(screen.getByTestId('tv-calendar-close').hasAttribute('data-home-chevron')).toBe(true);
        cleanup();
      }
    });
  }
});

describe('redirects', () => {
  for (const old of ['/settings/tv?view=libraries', '/settings/tv?view=options', '/settings/tv/libraries', '/settings/tv/options']) {
    it(`${old} -> ${TV_SETTINGS_PATH}`, async () => {
      setup(true);
      at(old);
      await screen.findByTestId('tv-settings');
      expect(loc).toBe(TV_SETTINGS_PATH);
    });
  }
  it('Home > Calendar (/settings/tv) still opens the calendar view', async () => {
    setup(true);
    at('/settings/tv');
    await screen.findByTestId('tv-tabs');
    expect(screen.getByTestId('tv-tab-calendar').getAttribute('aria-selected')).toBe('true');
    expect(loc).toBe('/settings/tv');
  });
});
