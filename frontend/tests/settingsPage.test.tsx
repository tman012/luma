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

  it('web: full page: logo back to Home, avatar, left list with Home screen active, Backdrop library only', async () => {
    wide = true; stubMatchMedia();
    const fetchMock = api(); vi.stubGlobal('fetch', fetchMock);
    renderAt('/settings');
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings/home-screen'));
    expect(screen.queryByTestId('app-sidebar')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Menu' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Home' }).getAttribute('href')).toBe('/');
    expect(screen.getByTestId('settings-logo')).toBeTruthy();
    const nav = screen.getByTestId('settings-nav');
    expect(nav.querySelector('h1')!.textContent).toBe('Settings');
    expect(nav.style.width).toBe('220px');
    const item = screen.getByRole('link', { name: 'Home screen' });
    expect(item.getAttribute('aria-current')).toBe('page');
    expect(item.style.height).toBe('40px');
    expect(item.style.background).toBe('rgba(59, 130, 246, 0.14)');
    expect(screen.getByRole('heading', { level: 2, name: 'Home screen' })).toBeTruthy();
    await screen.findByRole('button', { name: /Backdrop library/ });
    expect(screen.queryByTestId('libraries')).toBeNull();
    expect(screen.queryByTestId('settings-sheet')).toBeNull();
    expect(screen.getByRole('button', { name: 'Account' }).style.width).toBe('48px');
    fireEvent.click(screen.getByRole('link', { name: 'Home' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/'));
  });

  it('web: Kiosk > Backdrop library opens a list with a check on the selected one', async () => {
    wide = true; stubMatchMedia();
    const fetchMock = api(); vi.stubGlobal('fetch', fetchMock);
    renderAt('/settings/home-screen');
    const row = await screen.findByRole('button', { name: /Backdrop library/ });
    expect(screen.queryByTestId('libraries')).toBeNull();
    expect(screen.getByTestId('backdrop-value').textContent).toBe('Movies');
    fireEvent.click(row);
    const radios = screen.getAllByRole('radio');
    expect(radios.map(r => r.getAttribute('aria-checked'))).toEqual(['true', 'false']);
    fireEvent.click(radios[1]!);
    await waitFor(() => expect(screen.getByTestId('backdrop-value').textContent).toBe('4K Movies'));
    const put = fetchMock.mock.calls.find(c => c[1]?.method === 'PUT' && String(c[0]).includes('/api/home/kiosk'))!;
    expect(JSON.parse(String(put[1]!.body))).toEqual({ id: '9' });
  });

  it('phone: /settings is a sheet with a circular X and the section list; rows push in with a back chevron', async () => {
    wide = false; stubMatchMedia(); plexOwner = false;
    vi.stubGlobal('fetch', api());
    renderAt('/settings');
    const sheet = screen.getByTestId('settings-sheet');
    expect(sheet.getAttribute('role')).toBe('dialog');
    expect(sheet.style.background).toBe('var(--color-kiosk-black)');   // app navy token, no new hex
    expect(sheet.style.borderRadius).toBe('20px 20px 0 0');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Settings');
    expect(screen.queryByRole('button', { name: 'Account' })).toBeNull();
    const close = screen.getByRole('button', { name: 'Close' });
    expect(close.style.width).toBe('44px');
    expect((close.firstChild as HTMLElement).style.width).toBe('36px');
    const list = screen.getByTestId('settings-list');
    expect([...list.querySelectorAll('a')].map(a => a.textContent)).toEqual(['Home screen']);
    const row = screen.getByRole('link', { name: 'Home screen' });
    expect(row.style.height).toBe('56px');
    fireEvent.click(row);
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings/home-screen'));
    await screen.findByTestId('libraries');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Home screen');
    const libsHead = screen.getByRole('heading', { level: 2, name: 'Libraries' });
    expect(libsHead.style.fontSize).toBe('20px');
    expect(libsHead.style.fontWeight).toBe('700');
    expect(screen.queryByTestId('kiosk-library')).toBeNull();          // no Kiosk group on phone
    expect(screen.getAllByTestId('library-row')[0]!.style.height).toBe('56px');
    expect(screen.getAllByRole('switch')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings'));
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/'));
  });

  it('phone: owners also get a Plex row', async () => {
    wide = false; stubMatchMedia(); vi.stubGlobal('fetch', api());
    renderAt('/settings');
    expect(await screen.findByRole('link', { name: 'Plex' })).toBeTruthy();
  });

  it('non-admins get no kiosk picker on web', async () => {
    wide = true; stubMatchMedia(); auth.user.is_admin = false;
    const fetchMock = api(); vi.stubGlobal('fetch', fetchMock);
    renderAt('/settings/home-screen');
    await screen.findByTestId('settings-nav');
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
    expect(screen.getByRole('heading', { level: 2, name: 'Plex' })).toBeTruthy();
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
