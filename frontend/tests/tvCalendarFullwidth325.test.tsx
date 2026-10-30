/** v325 (Taylor): on desktop TV Calendar is its own full-width view with no Settings nav; an up arrow
 * at the top-left (in the logo's place) closes it to Home. Other TV tabs keep the Settings layout. */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { BrowserRouter, MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

const auth = { authenticated: true, loading: false, implicit: false, logout: vi.fn(),
  user: { id: 1, name: 'taylor', is_admin: true } };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('@/components/library/TvLibrarySettings', () => ({ TvLibrarySettings: () => <div data-testid="tv-stub" /> }));
vi.mock('@/components/settings/SectionTrackersList', () => ({ SectionTrackersList: () => <div /> }));
vi.mock('@/minimal', async orig => ({ ...(await orig<typeof import('@/minimal')>()), isMinimal: () => true }));

import { SettingsPage } from '@/pages/LibrariesPage';

const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
function setup(wide: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px') ? wide : false, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }));
  vi.stubGlobal('fetch', vi.fn(async (url: string) => String(url).includes('/api/settings/plex')
    ? json({ connected: true, server: 'x', owner: true }) : json({ libraries: [], paths: [], owner: true })));
}
const routes = <Routes>
  <Route path="/" element={<div data-testid="home" />} />
  <Route path="/settings/:section" element={<SettingsPage />} />
  <Route path="/settings/:section/*" element={<SettingsPage />} />
</Routes>;
const at = (path: string) => render(<MemoryRouter initialEntries={['/elsewhere', path]} initialIndex={1}>{routes}</MemoryRouter>);

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('desktop TV Calendar full-width', () => {
  it('/settings/tv (Calendar, the default view) opens full-width: no Settings nav, the Home logo and the TV Calendar title', async () => {
    setup(true);
    at('/settings/tv');
    const close = await screen.findByTestId('tv-home-link');
    expect(screen.getByTestId('tv-stub')).toBeTruthy();
    expect(screen.queryByTestId('settings-nav')).toBeNull();
    expect(screen.queryByTestId('settings-logo')).toBeNull();
    expect(close.getAttribute('aria-label')).toBe('Home');                 // v319: Home's logo, not an up arrow
    expect(close.getAttribute('href')).toBe('/');
    expect(close.closest('header')).toBeTruthy();
    expect(close.querySelector('[data-testid=tv-logo]')!.getAttribute('alt')).toBe('Luma');
    expect(document.querySelector('[data-home-chevron], .a1-chev')).toBeNull();
    expect(screen.getByTestId('tv-page-title').textContent).toBe('TV Calendar');
    expect(screen.getByTestId('settings-main').style.maxWidth).toBe('');   // takes the full width
  });

  it('v327: every TV view (Series, Calendar, Import, a show page) is full-width with no Settings nav; Settings > TV keeps the Settings layout', async () => {
    setup(true);
    for (const p of ['/settings/tv?view=calendar', '/settings/tv?view=series', '/settings/tv?view=import', '/settings/tv?view=series&show=4']) {
      at(p);
      expect(await screen.findByTestId('tv-home-link')).toBeTruthy();
      expect(screen.queryByTestId('settings-nav')).toBeNull();
      cleanup();
    }
    for (const p of ['/settings/tv/settings', '/settings/tv?view=options', '/settings/tv?view=libraries']) {
      at(p);
      expect(await screen.findByTestId('settings-nav')).toBeTruthy();
      expect(screen.queryByTestId('tv-home-link')).toBeNull();
      expect(screen.getByTestId('settings-logo')).toBeTruthy();
      cleanup();
    }
  });

  it('the logo always goes Home (not history back)', async () => {
    setup(true);
    at('/settings/tv');
    fireEvent.click(await screen.findByTestId('tv-home-link'));
    expect(await screen.findByTestId('home')).toBeTruthy();
  });

  it('phone is unchanged: no desktop logo link', async () => {
    setup(false);
    at('/settings/tv');
    await screen.findByTestId('tv-stub');
    expect(screen.queryByTestId('tv-home-link')).toBeNull();
  });

  it('LUMA_MINIMAL: the reveal row path (go() pushState /settings/tv, 7a01c4be) opens the calendar and the logo closes it', async () => {
    setup(true);
    window.history.replaceState({}, '', '/');
    render(<BrowserRouter>{routes}</BrowserRouter>);
    expect(screen.getByTestId('home')).toBeTruthy();
    act(() => { window.history.pushState({}, '', '/settings/tv'); window.dispatchEvent(new PopStateEvent('popstate')); });
    fireEvent.click(await screen.findByTestId('tv-home-link'));
    expect(await screen.findByTestId('home')).toBeTruthy();
    expect(window.location.pathname).toBe('/');
  });
});
