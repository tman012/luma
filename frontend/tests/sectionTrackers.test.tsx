import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const auth = { authenticated: true, loading: false, implicit: false, logout: vi.fn(),
  user: { id: 1, name: 'taylor', is_admin: true } };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
let minimalBuild = true;
vi.mock('@/minimal', async orig => ({ ...(await orig<typeof import('@/minimal')>()), isMinimal: () => minimalBuild }));

import { SectionTrackersList } from '@/components/settings/SectionTrackersList';
import type { SectionTracker } from '@/components/settings/sectionTrackersApi';
import { SettingsPage } from '@/pages/LibrariesPage';

const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
type Call = { url: string; method: string; body: { trackers: { id: number; enabled: boolean }[] } | null };
let calls: Call[] = [];
let state: SectionTracker[];
let failPut = false;

const base = (): SectionTracker[] => [
  { id: 1, name: 'IPTorrents', type_name: 'IPTorrents', enabled: true, global_enabled: true, has_category: true, budget: { used: 3, cap: 8 } },
  { id: 2, name: 'BeyondHD', type_name: 'BeyondHD', enabled: false, global_enabled: true, has_category: true, budget: { used: 0, cap: 8 } },
  { id: 3, name: 'Movies Only', type_name: 'Torznab', enabled: true, global_enabled: true, has_category: false, budget: null },
  { id: 4, name: 'Paused', type_name: 'Torznab', enabled: false, global_enabled: false, has_category: true, budget: null },
];

function server() {
  return vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, method, body });
    if (url.endsWith('/api/settings/sections/tv/trackers')) {
      if (method === 'PUT') {
        if (failPut) return res({ error: "Couldn't save" }, 500);
        const byId = new Map(state.map(t => [t.id, t]));
        state = body.trackers.map((t: { id: number; enabled: boolean }) => ({ ...byId.get(t.id)!, enabled: t.enabled }));
      }
      return res({ section: 'tv', trackers: state });
    }
    return res({}, 404);
  });
}

beforeEach(() => {
  calls = []; state = base(); failPut = false; minimalBuild = true;
  vi.stubGlobal('fetch', server());
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px'), media: q, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const rows = () => screen.getAllByTestId('section-tracker-row');
const names = () => screen.getAllByTestId('section-tracker-name').map(n => n.textContent);

describe('SectionTrackersList', () => {
  it('lists every tracker in order with its switch, status and budget', async () => {
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(rows()).toHaveLength(4));
    expect(screen.getByText('Trackers')).toBeTruthy();
    expect(names()).toEqual(['IPTorrents', 'BeyondHD', 'Movies Only', 'Paused']);
    const meta = screen.getAllByTestId('section-tracker-meta').map(m => m.textContent);
    expect(meta).toEqual(['IPTorrents · 3 of 8 today', 'BeyondHD', 'Torznab · No TV category', 'Torznab · Off in Trackers']);
    expect(screen.getAllByRole('switch').map(s => s.getAttribute('aria-checked'))).toEqual(['true', 'false', 'true', 'false']);
  });

  it('a switch saves the whole list in order', async () => {
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(rows()).toHaveLength(4));
    fireEvent.click(screen.getByRole('switch', { name: 'BeyondHD' }));
    await waitFor(() => expect(calls.filter(c => c.method === 'PUT')).toHaveLength(1));
    expect(calls.find(c => c.method === 'PUT')!.body).toEqual({ trackers: [
      { id: 1, enabled: true }, { id: 2, enabled: true }, { id: 3, enabled: true }, { id: 4, enabled: false }] });
    await waitFor(() => expect(screen.getByRole('switch', { name: 'BeyondHD' }).getAttribute('aria-checked')).toBe('true'));
  });

  it('ArrowUp/ArrowDown on the handle reorder and keep focus', async () => {
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(rows()).toHaveLength(4));
    const h = screen.getByRole('button', { name: 'Reorder BeyondHD' });
    h.focus();
    fireEvent.keyDown(h, { key: 'ArrowUp' });
    await waitFor(() => expect(names()).toEqual(['BeyondHD', 'IPTorrents', 'Movies Only', 'Paused']));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Reorder BeyondHD' }));
    fireEvent.keyDown(screen.getByRole('button', { name: 'Reorder BeyondHD' }), { key: 'ArrowUp' });   // already first
    expect(calls.filter(c => c.method === 'PUT')).toHaveLength(1);
  });

  it('a failed save reverts and says so', async () => {
    failPut = true;
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(rows()).toHaveLength(4));
    fireEvent.click(screen.getByRole('switch', { name: 'IPTorrents' }));
    await waitFor(() => expect(screen.getByText("Couldn't save")).toBeTruthy());
    expect(screen.getByRole('switch', { name: 'IPTorrents' }).getAttribute('aria-checked')).toBe('true');
  });

  it('empty and load failure', async () => {
    state = [];
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(screen.getByTestId('section-trackers-empty').textContent).toBe('No trackers'));
    cleanup();
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('down'); }));
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe("Couldn't load trackers"));
  });
});

describe('Settings > TV', () => {
  const page = (path: string) => render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/settings/:section?" element={<SettingsPage />} /></Routes>
    </MemoryRouter>);

  it('shows on luma-test builds for the owner, with the TV tracker list', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/api/settings/sections/tv/trackers')) return server()(url, init);
      if (url.includes('/api/settings/plex')) return res({ owner: true, configured: true });
      return res({}, 404);
    }));
    await act(async () => { page('/settings/tv'); });
    await waitFor(() => expect(screen.getByTestId('section-trackers-tv')).toBeTruthy());
    await waitFor(() => expect(rows()).toHaveLength(4));
  });

  it('is not offered on production builds', async () => {
    minimalBuild = false;
    await act(async () => { page('/settings/home-screen'); });
    expect(screen.queryByRole('link', { name: 'TV' })).toBeNull();
  });
});
