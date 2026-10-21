import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TYPES from './fixtures/trackerTypes.json';

const auth = { authenticated: true, loading: false, implicit: false, logout: vi.fn(),
  user: { id: 1, name: 'taylor', is_admin: true } };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));

import { SettingsPage } from '@/pages/LibrariesPage';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
let wide = true;
let trackersStatus = 200;
let calls: string[] = [];
function stubMatchMedia() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px') ? wide : false, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }));
}
function api() {
  return vi.fn(async (input: string) => {
    const url = String(input);
    calls.push(url);
    if (url.includes('/api/trackers')) {
      if (trackersStatus !== 200) return res({ error: 'Owner access required' }, trackersStatus);
      if (url.endsWith('/api/trackers/types')) return res({ types: TYPES });
      return res({ trackers: [{ id: 3, type: 'torznab', type_name: 'Torznab', protocol: 'torrent', name: 'Idx', enabled: true,
        url: 'https://idx.example', categories: [2000], priority: 25, api_key: '********',
        status: { state: 'ok', message: '', checked_at: 1 } }] });
    }
    if (url.includes('/api/settings/plex/pin')) return res({ status: 'none' });
    if (url.includes('/api/settings/plex')) return res({ connected: false, server: '' });
    if (url.includes('/api/settings/connectors')) return res({ tmdb: { set: false, last4: '' } });
    if (url.includes('/api/home/kiosk')) return res({ id: '1', libraries: [{ id: '1', title: 'Movies' }] });
    return res({ libraries: [] });
  });
}
function Where() { const l = useLocation(); return <p data-testid="where">{l.pathname + l.search}</p>; }
function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/settings" element={<SettingsPage />} />
    <Route path="/settings/:section" element={<SettingsPage />} />
    <Route path="/settings/:section/*" element={<SettingsPage />} />
    <Route path="/" element={<p>home</p>} />
  </Routes><Where /></MemoryRouter>);
}
const where = () => screen.getByTestId('where').textContent;

describe('Settings > Trackers wiring', () => {
  beforeEach(() => {
    auth.user.is_admin = true; auth.implicit = false; trackersStatus = 200; calls = [];
    vi.stubGlobal('fetch', api());
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('desktop: Trackers is the last section and draws its own title row', async () => {
    wide = true; stubMatchMedia();
    renderAt('/settings/trackers');
    const nav = screen.getByTestId('settings-nav');
    await waitFor(() => expect(within(nav).getAllByRole('link').map(a => a.textContent)).toEqual(['Home screen', 'Plex', 'Rooms', 'Connectors', 'Trackers']));
    expect(within(nav).getByRole('link', { name: 'Trackers' }).getAttribute('aria-current')).toBe('page');
    await screen.findByText('Idx');
    expect(screen.getAllByRole('heading', { level: 2, name: 'Trackers' })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Add tracker' })).toBeTruthy();
    fireEvent.click(screen.getByText('Idx'));
    await waitFor(() => expect(where()).toBe('/settings/trackers/3'));
    expect(await screen.findByTestId('tracker-form')).toBeTruthy();
    expect(within(screen.getByTestId('settings-nav')).getByRole('link', { name: 'Trackers' }).getAttribute('aria-current')).toBe('page');
    expect((screen.getByLabelText('API key') as HTMLInputElement).value).toBe('');
  });

  it('desktop: /settings/trackers/new opens the picker over the list', async () => {
    wide = true; stubMatchMedia();
    renderAt('/settings/trackers/new');
    expect(await screen.findByRole('dialog', { name: 'Add tracker' })).toBeTruthy();
    expect(screen.getByText('Idx')).toBeTruthy();
  });

  it('phone: a Trackers row; the section has its own header with Plus and no shell header', async () => {
    wide = false; stubMatchMedia();
    renderAt('/settings');
    const list = await screen.findByTestId('settings-list');
    await waitFor(() => expect(within(list).getByRole('link', { name: 'Trackers' })).toBeTruthy());
    fireEvent.click(within(list).getByRole('link', { name: 'Trackers' }));
    await screen.findByText('Idx');
    const header = screen.getByTestId('trackers-phone-header');
    expect(within(header).getByRole('button', { name: 'Add tracker' })).toBeTruthy();
    expect(screen.getAllByRole('heading', { name: 'Trackers' })).toHaveLength(1);
    fireEvent.click(within(header).getByRole('button', { name: 'Back' }));
    await waitFor(() => expect(where()).toBe('/settings'));
  });

  it.each([401, 403])('hidden and redirected when the API answers %i', async code => {
    wide = true; stubMatchMedia(); trackersStatus = code;
    renderAt('/settings/trackers');
    await waitFor(() => expect(where()).toBe('/settings/home-screen'));
    expect(within(screen.getByTestId('settings-nav')).queryByRole('link', { name: 'Trackers' })).toBeNull();
  });

  it('the implicit LAN owner and non-admins never ask for trackers', async () => {
    wide = true; stubMatchMedia();
    auth.implicit = true;
    const { unmount } = renderAt('/settings/trackers');
    await waitFor(() => expect(where()).toBe('/settings/home-screen'));
    unmount();
    auth.implicit = false; auth.user.is_admin = false;
    renderAt('/settings/trackers');
    await waitFor(() => expect(where()).toBe('/settings/home-screen'));
    expect(calls.some(u => u.includes('/api/trackers'))).toBe(false);
  });
});
