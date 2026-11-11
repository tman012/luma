/** Desktop Settings has no top bar: the logo, the 'Settings' heading and the avatar sit directly over the page
 * background (no backdrop, border or sticky strip), so the glow runs to the top edge. Phone is unchanged. */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

const auth = { authenticated: true, loading: false, implicit: false, logout: vi.fn(),
  user: { id: 1, name: 'taylor', is_admin: true } };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('@/components/library/LibraryPathsSettings', () => ({ LibraryPathsSettings: () => <div data-testid="lp-stub" /> }));
vi.mock('@/components/library/TvLibrarySettings', () => ({ TvLibrarySettings: () => <div data-testid="tv-stub" /> }));
vi.mock('@/components/settings/SectionTrackersList', () => ({ SectionTrackersList: () => <div data-testid="st-stub" /> }));
vi.mock('@/minimal', async orig => ({ ...(await orig<typeof import('@/minimal')>()), isMinimal: () => true }));

import { SettingsPage } from '@/pages/LibrariesPage';

const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
function setup(wide: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px') ? wide : false, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }));
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (String(url).includes('/api/settings/plex')) return json({ connected: false, server: '' });
    return json({ libraries: [], paths: [], rules: [], fallback_path_id: null, id: '1' });
  }));
}
const at = (path: string) => render(<MemoryRouter initialEntries={[path]}><Routes>
  <Route path="/settings" element={<SettingsPage />} />
  <Route path="/settings/:section" element={<SettingsPage />} />
  <Route path="/settings/:section/*" element={<SettingsPage />} />
</Routes></MemoryRouter>);

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('desktop Settings has no top bar', () => {
  it('the header row paints nothing and does not stick; no element on the page is sticky or fixed except the glow layer', async () => {
    setup(true);
    const v = at('/settings/library-paths');
    await screen.findByTestId('settings-nav');
    const header = screen.getByTestId('settings-logo').closest('header')!;
    const s = header.style;
    expect(['sticky', 'fixed']).not.toContain(s.position);
    expect([s.background, s.backgroundColor, s.backgroundImage, s.border, s.borderBottom, s.boxShadow, s.backdropFilter])
      .toEqual(['', '', '', '', '', '', '']);
    const glow = screen.getByTestId('glow-layer');
    const pinned = Array.from(v.container.querySelectorAll<HTMLElement>('*'))
      .filter(e => e !== glow && ['sticky', 'fixed'].includes(e.style.position));
    expect(pinned).toEqual([]);
    expect(v.container.querySelectorAll('header')).toHaveLength(1);
  });

  it('the logo, the Settings heading and the avatar are present', async () => {
    setup(true);
    at('/settings/library-paths');
    const nav = await screen.findByTestId('settings-nav');
    const header = screen.getByTestId('settings-logo').closest('header')!;
    expect(screen.getByTestId('settings-logo').getAttribute('alt')).toBe('Luma');
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBe(nav.firstElementChild);
    const avatar = screen.getByRole('button', { name: 'Account' });
    expect(header.contains(avatar)).toBe(true);
    expect(header.lastElementChild!.contains(avatar)).toBe(true);
  });

  it('the avatar still opens the account menu: Settings and Sign out', async () => {
    setup(true);
    at('/settings/library-paths');
    await screen.findByTestId('settings-nav');
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Account' }));
    const menu = await screen.findByTestId('user-menu');
    expect(Array.from(menu.querySelectorAll('[role="menuitem"]')).map(e => e.textContent)).toEqual(['Settings', 'Sign out']);
  });

  it('phone keeps its sheet header and has no desktop header', async () => {
    setup(false);
    at('/settings');
    await screen.findByTestId('settings-sheet');
    expect(screen.queryByTestId('settings-logo')).toBeNull();
    expect(screen.queryByTestId('settings-nav')).toBeNull();
  });
});
