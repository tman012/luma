/** v358, LUMA_MINIMAL=1 (production): the real minimal app (App.tsx router, real Home and Settings pages, mocked
 * network). Every button the nav bar draws opens a destination that renders; none is a dead button. */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The voice module primes an AudioContext on the first click; jsdom has none.
class FakeAudioContext { state = 'running'; resume() { return Promise.resolve(); } createBuffer() { return {}; } createBufferSource() { return { connect() {}, start() {}, buffer: null }; } destination = {}; }
(globalThis as unknown as { AudioContext: unknown }).AudioContext ??= FakeAudioContext;

import App from '@/App';
import { desktopDestinations } from '@/components/home/DesktopDock';
import { isMinimal } from '@/minimal';

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
const calls: string[] = [];
const SHOWS = [{ id: 4, tmdb_id: 90228, title: 'Sample Show', year: 2024, path: '/media/tv/Sample Show', episodes: 8, files: 8, monitored: true, seasons: [] }];
function stubNetwork() {
  vi.stubGlobal('fetch', vi.fn(async (u: string | URL | Request) => {
    const url = String(typeof u === 'object' && 'url' in u ? u.url : u);
    const path = url.replace(/^https?:\/\/[^/]+/, '').split('?')[0]!;
    calls.push(path);
    if (path === '/api/auth/me') return json({ authenticated: true, user: { id: 1, name: 'taylor', is_admin: true }, user_settings: {}, implicit: false });
    if (path === '/api/hero/content') return json({ success: true, movies: [{ rating_key: 101, title: 'Sample Feature', year: 2026, summary: 'Plot.', content_rating: 'PG-13',
      duration: 8040000, genres: ['Drama'], backdrop_url: '/api/img/plex?url=/library/metadata/101/art/1' }] });
    if (path === '/api/settings/plex') return json({ connected: true, server: 'x', owner: true });
    if (path === '/api/settings/tv/library') return json({ shows: SHOWS, unmatched: [], scan: {} });
    if (path === '/api/settings/tv') return json({ paths: [{ id: 'p1', name: 'TV', path: '/media/tv', status: 'ok' }], upgrades: { mode: 'off' }, rescan_interval_h: 6 });
    if (path === '/api/movies/watchlist/tmdb-ids') return json({ tmdb_ids: [] });
    // Outside the minimal core the server answers 404 (app/minimal.py). Nothing here may depend on those.
    if (/^\/api\/(watchlist|library\/browse|library\/genres|trakt|explore|in-theaters|sonarr|radarr)/.test(path)) return json({}, 404);
    return json({});
  }));
}
function stubWide(wide: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px') ? wide : false, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }));
}

beforeEach(() => {
  calls.length = 0;
  window.__LUMA_MINIMAL__ = true;
  window.history.replaceState({}, '', '/');
  stubNetwork();
});
afterEach(() => {
  cleanup();
  delete window.__LUMA_MINIMAL__;
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
  document.documentElement.classList.remove('luma-minimal');
  document.documentElement.style.overflow = ''; document.body.style.overflow = '';
});

const bar = () => screen.findByTestId('desktop-dock', undefined, { timeout: 5000 });
const barNames = (el: HTMLElement) => [...el.querySelectorAll('button, a')].map(x => x.textContent);
const where = () => window.location.pathname + window.location.search;

describe('minimal mode on: the desktop Home nav bar', () => {
  beforeEach(() => stubWide(true));

  it('minimal mode is really on, and Home draws the bar with exactly the buttons that have a page', async () => {
    render(<App />);
    expect(isMinimal()).toBe(true);
    expect(document.documentElement.classList.contains('luma-minimal')).toBe(true);
    const dock = await bar();
    expect(screen.getByTestId('kiosk-header')).toBeTruthy();
    expect(barNames(dock)).toEqual(['Search', 'Libraries', 'Calendar']);
    expect(barNames(dock)).toEqual(['Search', ...desktopDestinations(true).map(d => d.name)]);
    expect(dock.querySelector('[disabled], [aria-disabled]')).toBeNull();
  });

  it('Search opens the search modal over Home (stays on /), Escape closes it and focus is back on Search', async () => {
    render(<App />);
    const dock = await bar();
    const search = dock.querySelector<HTMLButtonElement>('[data-testid="dock-search"]')!;
    fireEvent.click(search);
    const dialog = screen.getByRole('dialog', { name: 'Search' });
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(document.activeElement).toBe(screen.getByTestId('search-input'));
    expect(where()).toBe('/');
    expect(screen.queryByTestId('search-palette')).toBeNull();   // one search modal, not the palette as well
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.getByTestId('r3-modal').dataset.open).toBe('false');
    expect(document.activeElement).toBe(search);
  });

  it('Calendar navigates to /settings/tv and the TV Calendar page renders (without the bar)', async () => {
    render(<App />);
    const dock = await bar();
    fireEvent.click(dock.querySelector('[data-testid="dock-calendar"]')!);
    expect(where()).toBe('/settings/tv');
    const section = await screen.findByTestId('tv-section', undefined, { timeout: 5000 });
    expect(section.dataset.view).toBe('calendar');
    expect(screen.getByTestId('tv-page-title').textContent).toBe('TV Calendar');
    expect(document.querySelector('[data-step="settings"][data-layout="desktop"]')).toBeTruthy();
    expect(screen.queryByTestId('kiosk-header')).toBeNull();     // Home is gone: this is a page change, not a no-op
    expect(screen.queryByTestId('desktop-dock')).toBeNull();     // no bar on Settings routes
    expect(where()).toBe('/settings/tv');                        // and no redirect back Home
  });

  it('Libraries navigates to the TV Series library (/settings/tv?view=series) and it renders (without the bar)', async () => {
    render(<App />);
    const dock = await bar();
    fireEvent.click(dock.querySelector('[data-testid="dock-libraries"]')!);
    expect(where()).toBe('/settings/tv?view=series');
    const section = await screen.findByTestId('tv-section', undefined, { timeout: 5000 });
    expect(section.dataset.view).toBe('series');
    await waitFor(() => expect(screen.getByText('Sample Show')).toBeTruthy());
    expect(screen.queryByTestId('kiosk-header')).toBeNull();
    expect(screen.queryByTestId('desktop-dock')).toBeNull();
    expect(where()).toBe('/settings/tv?view=series');
  });

  it('the logo on the destination goes back Home, where the bar is drawn again', async () => {
    render(<App />);
    fireEvent.click((await bar()).querySelector('[data-testid="dock-calendar"]')!);
    fireEvent.click(await screen.findByTestId('tv-home-link', undefined, { timeout: 5000 }));
    expect(where()).toBe('/');
    expect(barNames(await bar())).toEqual(['Search', 'Libraries', 'Calendar']);
  });

  it.each(['/browse', '/explore', '/watchlist'])('%s has no page in the minimal app (it lands on Home), so the bar draws no button for it', async (path) => {
    window.history.replaceState({}, '', path);
    render(<App />);
    const dock = await bar();
    await waitFor(() => expect(where()).toBe('/'));
    expect(screen.getByTestId('kiosk-header')).toBeTruthy();
    expect([...dock.querySelectorAll('a')].map(a => a.getAttribute('href'))).toEqual(['/settings/tv?view=series', '/settings/tv']);
    expect(dock.querySelector(`a[href="${path}"]`)).toBeNull();
  });

  it('every Settings screen in the minimal app renders without the bar', async () => {
    for (const path of ['/settings/home-screen', '/settings/tv/settings', '/settings/tv', '/settings/connectors']) {
      window.history.replaceState({}, '', path);
      const view = render(<App />);
      await waitFor(() => expect(document.querySelector('[data-step="settings"][data-layout="desktop"]')).toBeTruthy(), { timeout: 5000 });
      expect(screen.queryByTestId('desktop-dock')).toBeNull();
      view.unmount();
    }
  });

  it('nothing the bar opens depends on a route minimal mode keeps closed', async () => {
    render(<App />);
    const dock = await bar();
    fireEvent.click(dock.querySelector('[data-testid="dock-libraries"]')!);
    await screen.findByTestId('tv-section', undefined, { timeout: 5000 });
    await act(async () => { await new Promise(r => setTimeout(r, 50)); });
    expect(calls.filter(p => /^\/api\/(watchlist|library\/browse|library\/genres|trakt|explore|in-theaters|sonarr|radarr)/.test(p))).toEqual([]);
  });
});

describe('minimal mode on: phones are unchanged', () => {
  it('below 1024px there is no nav bar: the phone header and its search button, as before', async () => {
    stubWide(false);
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('phone-header')).toBeTruthy(), { timeout: 5000 });
    expect(screen.getByTestId('search-button')).toBeTruthy();
    expect(screen.queryByTestId('desktop-dock')).toBeNull();
    expect(screen.queryByTestId('r3-search')).toBeNull();
    expect(document.querySelector('.vgb, .vgb-ruler')).toBeNull();
  });
});
