import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const auth = { authenticated: true, loading: false, user: { id: 1, name: 'taylor', is_admin: true } };
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
function api() {
  return vi.fn(async (url: string, init?: RequestInit) => {
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
  beforeEach(() => { auth.user.is_admin = true; });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('web: /settings opens Home screen beside a left nav, with the kiosk picker inside it', async () => {
    wide = true; stubMatchMedia();
    const fetchMock = api(); vi.stubGlobal('fetch', fetchMock);
    renderAt('/settings');
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings/home-screen'));
    const nav = screen.getByTestId('settings-nav');
    const item = screen.getByRole('link', { name: 'Home screen' });
    expect(nav.contains(item)).toBe(true);
    expect(item.getAttribute('aria-current')).toBe('page');
    expect(parseInt(item.style.minHeight, 10)).toBeGreaterThanOrEqual(44);
    await waitFor(() => screen.getByTestId('libraries'));
    const picker = await screen.findByTestId('kiosk-library');
    expect(screen.getByTestId('home-screen-section').parentElement!.contains(picker)).toBe(true);
    const radios = await screen.findAllByRole('radio');
    expect(radios.map(r => [r.textContent, r.getAttribute('aria-checked')])).toEqual([['Movies', 'true'], ['4K Movies', 'false']]);
    fireEvent.click(radios[1]!);
    await waitFor(() => expect(screen.getAllByRole('radio')[1]!.getAttribute('aria-checked')).toBe('true'));
    const put = fetchMock.mock.calls.find(c => c[1]?.method === 'PUT' && String(c[0]).includes('/api/home/kiosk'))!;
    expect(JSON.parse(String(put[1]!.body))).toEqual({ id: '9' });
  });

  it('phone: /settings lists sections; a section drills in with a back control', async () => {
    wide = false; stubMatchMedia();
    vi.stubGlobal('fetch', api());
    renderAt('/settings');
    expect(screen.getByTestId('settings-sections')).toBeTruthy();
    expect(screen.queryByTestId('libraries')).toBeNull();
    const entry = screen.getByRole('link', { name: 'Home screen' });
    expect(parseInt(entry.style.minHeight, 10)).toBeGreaterThanOrEqual(44);
    fireEvent.click(entry);
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings/home-screen'));
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Home screen');
    await screen.findByTestId('libraries');
    await screen.findByTestId('kiosk-library');
    fireEvent.click(screen.getByRole('link', { name: 'Settings' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings'));
  });

  it('the kiosk picker is owner/admin only', async () => {
    wide = true; stubMatchMedia(); auth.user.is_admin = false;
    const fetchMock = api(); vi.stubGlobal('fetch', fetchMock);
    renderAt('/settings/home-screen');
    await screen.findByTestId('libraries');
    expect(screen.queryByTestId('kiosk-library')).toBeNull();
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/api/home/kiosk'))).toBe(false);
  });

  it('an unknown section goes back to /settings', async () => {
    wide = true; stubMatchMedia(); vi.stubGlobal('fetch', api());
    renderAt('/settings/nope');
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings/home-screen'));
  });
});
