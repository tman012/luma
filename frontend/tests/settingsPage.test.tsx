import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const auth = { authenticated: true, loading: false, implicit: false, logout: vi.fn(),
  user: { id: 1, name: 'taylor', is_admin: true } };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));

import { SettingsPage } from '@/pages/LibrariesPage';

const json = (body: unknown, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => body }) as Response;
let wide = true;
function stubMatchMedia() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px') ? wide : false, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }));
}
const LIBS = [{ id: '1', title: 'Movies', type: 'movie', enabled: true }, { id: '2', title: 'TV Shows', type: 'show', enabled: true }];
const KIOSK = { id: '1', libraries: [{ id: '1', title: 'Movies' }, { id: '9', title: '4K Movies' }] };
let plexOwner = true;
let plexConnected = false;
function api() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes('/api/settings/plex/pin')) return json({ status: 'none' });
    if (String(url).includes('/api/settings/plex')) return plexOwner ? json({ connected: plexConnected, server: plexConnected ? 'Basement' : '' }) : json({ error: 'Owner only' }, false);
    if (String(url).includes('/api/home/kiosk')) {
      if (init?.method === 'PUT') return json({ ...KIOSK, id: JSON.parse(String(init.body)).id });
      return json(KIOSK);
    }
    return json({ libraries: LIBS });
  });
}
function Where() { return <p data-testid="where">{useLocation().pathname}</p>; }
function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/settings" element={<SettingsPage />} />
    <Route path="/settings/:section" element={<SettingsPage />} />
    <Route path="/" element={<p>home</p>} />
  </Routes><Where /></MemoryRouter>);
}

describe('Settings', () => {
  beforeEach(() => { auth.user.is_admin = true; plexOwner = true; plexConnected = false; });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('web: persistent sidebar (Home, Settings > Home screen active) beside the section', async () => {
    wide = true; stubMatchMedia();
    vi.stubGlobal('fetch', api());
    renderAt('/settings');
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings/home-screen'));
    const side = screen.getByTestId('app-sidebar');
    expect(side.style.width).toBe('220px');
    expect(side.style.position).toBe('fixed');
    expect((screen.getByTestId('nav-logo') as HTMLImageElement).style.height).toBe('40px');
    const home = screen.getByRole('link', { name: 'Home' });
    const settings = screen.getByRole('button', { name: 'Settings' });
    const sub = screen.getByRole('link', { name: 'Home screen' });
    for (const el of [home, settings, sub]) expect(side.contains(el)).toBe(true);
    expect(parseInt(home.style.height, 10)).toBe(44);
    expect(parseInt(sub.style.height, 10)).toBe(36);
    expect(settings.getAttribute('aria-expanded')).toBe('true');   // auto-expanded on /settings routes
    expect(sub.getAttribute('aria-current')).toBe('page');
    expect(home.getAttribute('aria-current')).toBeNull();
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Home screen');
    expect(screen.getByRole('button', { name: 'Account' }).style.width).toBe('48px');
    expect(screen.queryByText('Back')).toBeNull();
    fireEvent.click(settings);   // click toggles
    expect(screen.queryByRole('link', { name: 'Home screen' })).toBeNull();
    fireEvent.click(settings);
    expect(screen.getByRole('link', { name: 'Home screen' })).toBeTruthy();
  });

  it('web: Kiosk > Backdrop library opens a list with a check on the selected one', async () => {
    wide = true; stubMatchMedia();
    const fetchMock = api(); vi.stubGlobal('fetch', fetchMock);
    renderAt('/settings/home-screen');
    await screen.findByTestId('libraries');
    const row = await screen.findByRole('button', { name: /Backdrop library/ });
    expect(screen.getByTestId('backdrop-value').textContent).toBe('Movies');
    fireEvent.click(row);
    const radios = screen.getAllByRole('radio');
    expect(radios.map(r => [r.textContent, r.getAttribute('aria-checked')])).toEqual([['Movies', 'true'], ['4K Movies', 'false']]);
    fireEvent.click(radios[1]!);
    await waitFor(() => expect(screen.getByTestId('backdrop-value').textContent).toBe('4K Movies'));
    const put = fetchMock.mock.calls.find(c => c[1]?.method === 'PUT' && String(c[0]).includes('/api/home/kiosk'))!;
    expect(JSON.parse(String(put[1]!.body))).toEqual({ id: '9' });
  });

  it('phone: header hamburger + title + avatar; drawer has the same structure; no kiosk picker', async () => {
    wide = false; stubMatchMedia();
    vi.stubGlobal('fetch', api());
    renderAt('/settings/home-screen');
    await screen.findByTestId('libraries');
    expect(screen.queryByTestId('app-sidebar')).toBeNull();
    expect(screen.queryByTestId('kiosk-library')).toBeNull();
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Home screen');
    expect(screen.queryByTestId('nav-drawer')).toBeNull();
    const menu = screen.getByRole('button', { name: 'Menu' });
    expect(menu.style.width).toBe('44px');
    await act(async () => { fireEvent.click(menu); await new Promise(r => requestAnimationFrame(() => r(null))); });
    const drawer = screen.getByTestId('nav-drawer');
    expect(drawer.getAttribute('role')).toBe('dialog');
    expect(drawer.getAttribute('aria-modal')).toBe('true');
    expect(drawer.style.width).toBe('280px');
    expect(parseInt(screen.getByRole('link', { name: 'Home' }).style.height, 10)).toBe(48);
    expect(parseInt(screen.getByRole('link', { name: 'Home screen' }).style.height, 10)).toBe(44);
    expect(document.documentElement.style.overflow).toBe('hidden');   // body scroll locked
    expect(drawer.contains(document.activeElement)).toBe(true);        // focus moved in
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('nav-drawer')).toBeNull());
  });

  it('drawer closes on scrim tap and on selecting an item', async () => {
    wide = false; stubMatchMedia(); vi.stubGlobal('fetch', api());
    renderAt('/settings/home-screen');
    await screen.findByTestId('libraries');
    fireEvent.click(screen.getByRole('button', { name: 'Menu' }));
    fireEvent.click(screen.getByTestId('nav-scrim'));
    await waitFor(() => expect(screen.queryByTestId('nav-drawer')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Menu' }));
    fireEvent.click(screen.getByRole('link', { name: 'Home' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/'));
  });

  it('non-admins get no Settings entry and no kiosk picker', async () => {
    wide = true; stubMatchMedia(); auth.user.is_admin = false;
    const fetchMock = api(); vi.stubGlobal('fetch', fetchMock);
    renderAt('/settings/home-screen');
    await screen.findByTestId('libraries');
    expect(screen.getByRole('link', { name: 'Home' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Settings' })).toBeNull();
    expect(screen.queryByTestId('kiosk-library')).toBeNull();
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/api/home/kiosk'))).toBe(false);
  });

  it('an unknown section goes to Home screen', async () => {
    wide = true; stubMatchMedia(); vi.stubGlobal('fetch', api());
    renderAt('/settings/nope');
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings/home-screen'));
  });

  it('Plex: an owner-only sub-item under Settings; Not connected + Connect Plex', async () => {
    wide = true; stubMatchMedia(); vi.stubGlobal('fetch', api());
    renderAt('/settings/plex');
    const item = await screen.findByRole('link', { name: 'Plex' });
    expect(item.getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Plex');
    expect((await screen.findByTestId('plex-server-name')).textContent).toBe('Not connected');
    const btn = await screen.findByRole('button', { name: 'Connect Plex' });
    expect(parseInt(btn.style.minHeight, 10)).toBeGreaterThanOrEqual(44);
  });

  it('non-owners: no Plex sub-item, and /settings/plex goes to Home screen', async () => {
    wide = true; stubMatchMedia(); plexOwner = false; vi.stubGlobal('fetch', api());
    renderAt('/settings/plex');
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings/home-screen'));
    expect(screen.queryByRole('link', { name: 'Plex' })).toBeNull();
    expect(screen.queryByTestId('plex-settings')).toBeNull();
  });

  it("Change server asks 'Change server?' with Cancel and Change first", async () => {
    wide = true; stubMatchMedia(); plexConnected = true;
    const fetchMock = api(); vi.stubGlobal('fetch', fetchMock);
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    renderAt('/settings/plex');
    expect((await screen.findByTestId('plex-server-name')).textContent).toBe('Basement');
    fireEvent.click(await screen.findByRole('button', { name: 'Change server' }));
    const dlg = screen.getByRole('alertdialog');
    expect(dlg.textContent).toBe('Change server?CancelChange');   // no extra text
    expect(document.activeElement?.textContent).toBe('Cancel');
    const pinPosts = () => fetchMock.mock.calls.filter(c => String(c[0]).includes('/api/settings/plex/pin') && c[1]?.method === 'POST').length;
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Change server' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(pinPosts()).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: 'Change server' }));
    fireEvent.click(screen.getByRole('button', { name: 'Change' }));
    await waitFor(() => expect(pinPosts()).toBe(1));
    expect(open).toHaveBeenCalled();   // the Plex pop-up opens inside the Change click
    open.mockRestore();
  });

  it('Connect Plex (not yet connected) starts sign-in without a confirm', async () => {
    wide = true; stubMatchMedia();
    const fetchMock = api(); vi.stubGlobal('fetch', fetchMock);
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    renderAt('/settings/plex');
    fireEvent.click(await screen.findByRole('button', { name: 'Connect Plex' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    await waitFor(() => expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/api/settings/plex/pin') && c[1]?.method === 'POST')).toBe(true));
    open.mockRestore();
  });
});
