/** Designer: on desktop Settings the heading and nav sit directly under the
 * Luma logo (same left edge, 16px below it), the content follows at the same
 * nav-to-content gap and TV takes the freed width. Phone is unchanged. */
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

const auth = { authenticated: true, loading: false, implicit: false, logout: vi.fn(),
  user: { id: 1, name: 'taylor', is_admin: true } };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
// Layout only: the panels are stubs (their own tests cover them).
vi.mock('@/components/library/LibraryPathsSettings', () => ({ LibraryPathsSettings: () => <div data-testid="lp-stub" /> }));
// TV draws its own title row ("TV" and the Calendar | Import | Libraries | Options control, docs/design/tv.md §1).
vi.mock('@/components/library/TvLibrarySettings', () => ({ TvLibrarySettings: () => <div data-testid="tv-stub"><h2>TV</h2></div> }));
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
</Routes></MemoryRouter>);

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('desktop Settings layout', () => {
  it('heading and nav share the logo left edge, 16px under the logo; header unchanged otherwise', async () => {
    setup(true);
    at('/settings/library-paths');
    const nav = await screen.findByTestId('settings-nav');
    await screen.findByRole('heading', { level: 2, name: 'Library paths' });
    const header = screen.getByTestId('settings-logo').closest('header')!;
    const page = nav.parentElement!;
    // logo left edge = header left padding; heading/nav left edge = page left padding
    expect(header.style.paddingLeft).toBe('44px');
    expect(page.style.paddingLeft).toBe(header.style.paddingLeft);
    // logo bottom = header content bottom (no bottom padding); heading top = page top padding
    expect(header.style.paddingBottom).toBe('0px');
    expect(page.style.paddingTop).toBe('16px');
    expect(nav.firstElementChild!.tagName).toBe('H1');
    expect((nav.firstElementChild as HTMLElement).style.margin).toBe('0px 0px 20px');
    expect([header.style.paddingTop, header.style.paddingRight, header.style.justifyContent]).toEqual(['20px', '60px', 'space-between']);
    expect(header.lastElementChild!.querySelector('[aria-label="Account"]') ?? header.lastElementChild).toBeTruthy();   // avatar last (top-right)
    // no centering container: nothing pushes the columns back toward the middle
    expect([page.style.maxWidth, page.style.margin]).toEqual(['', '']);
  });

  it('content follows at the same 40px gap; TV takes the freed width, the panel title on the heading line', async () => {
    setup(true);
    const v = at('/settings/tv?view=series')   // v325: Calendar (the default) is full-width, no nav;
    const nav = await screen.findByTestId('settings-nav');
    await screen.findByRole('heading', { level: 2, name: 'TV' });
    const page = nav.parentElement!;
    const main = screen.getByTestId('settings-main');
    expect(page.style.gap).toBe('40px');
    expect(nav.style.width).toBe('220px');
    expect(main.style.maxWidth).toBe('');
    expect(main.style.flex).toBe('1 1 0%');
    // both columns start at the row top: the panel title and the heading share a line
    expect(page.style.alignItems).toBe('');
    expect(main.firstElementChild!.getAttribute('data-testid')).toBe('tv-stub');   // the TV panel's own title row, no second title
    expect(main.querySelectorAll('h2')).toHaveLength(1);
    v.unmount();
    at('/settings/library-paths');
    await waitFor(() => expect(screen.getByTestId('settings-main').style.maxWidth).toBe('720px'));
  });

  it('v252: Automation is wider (settings and status side by side, GR5)', async () => {
    setup(true);
    at('/settings/automation');
    await screen.findByTestId('settings-nav');
    await waitFor(() => expect(screen.getByTestId('settings-main').style.maxWidth).toBe('1120px'));
  });

  it('Rooms takes the freed width too (cards 3 per row), as TV', async () => {
    setup(true);
    at('/settings/rooms');
    await screen.findByTestId('settings-nav');
    await waitFor(() => expect(screen.getByTestId('settings-main').style.maxWidth).toBe(''));
  });

  it('phone is unchanged', async () => {
    setup(false);
    const v = at('/settings');
    await screen.findByTestId('settings-sheet');
    expect(v.container.innerHTML).toMatchSnapshot('phone list');
    v.unmount();
    const w = at('/settings/library-paths');
    await screen.findByRole('heading', { name: 'Library paths' });
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await new Promise(r => setTimeout(r, 50));
    expect(w.container.innerHTML).toMatchSnapshot('phone library paths');
  });
});
