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
      // Like the server: active = saved on and usable.
      return res({ section: 'tv', trackers: state.map(t => ({ ...t, active: t.enabled && t.global_enabled && t.has_category })) });
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
    const meta = rows().map(r => r.querySelector('[data-testid="section-tracker-meta"]')?.textContent ?? '');
    // The type is left out when it is the name; an empty line isn't drawn.
    expect(meta).toEqual(['3 of 8 today', '', 'Torznab · No TV category', 'Torznab · Off in Trackers']);
    // Movies Only is saved on but has no TV category: it shows off (QA-TV2).
    expect(screen.getAllByRole('switch').map(s => s.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false', 'false']);
  });

  it('unusable rows show off and locked at 40%, still focusable, and only switching on is blocked', async () => {
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(rows()).toHaveLength(4));
    const sw = (n: string) => screen.getByRole('switch', { name: n }) as HTMLButtonElement;
    for (const n of ['Movies Only', 'Paused']) {
      expect(sw(n).disabled).toBe(false);                       // reachable by keyboard
      expect(sw(n).getAttribute('aria-disabled')).toBe('true');
      expect(sw(n).getAttribute('aria-checked')).toBe('false');
      expect(sw(n).style.opacity).toBe('0.4');
      expect(document.getElementById(sw(n).getAttribute('aria-describedby')!)!.textContent)
        .toMatch(/No TV category|Off in Trackers/);
      sw(n).focus();
      expect(document.activeElement).toBe(sw(n));
      fireEvent.click(sw(n));
      fireEvent.keyDown(sw(n), { key: ' ' });
    }
    for (const n of ['IPTorrents', 'BeyondHD']) {
      expect(sw(n).getAttribute('aria-disabled')).toBeNull();
      expect(sw(n).style.opacity).toBe('1');
    }
    await new Promise(r => setTimeout(r, 20));
    expect(calls.filter(c => c.method === 'PUT')).toHaveLength(0);
  });

  it('saving the list keeps an unusable row\'s saved switch underneath', async () => {
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(rows()).toHaveLength(4));
    fireEvent.click(screen.getByRole('switch', { name: 'BeyondHD' }));
    await waitFor(() => expect(calls.filter(c => c.method === 'PUT')).toHaveLength(1));
    expect(calls.find(c => c.method === 'PUT')!.body!.trackers.find(t => t.id === 3)).toEqual({ id: 3, enabled: true });
    await waitFor(() => expect(screen.getByRole('switch', { name: 'BeyondHD' }).getAttribute('aria-checked')).toBe('true'));
    expect(screen.getByRole('switch', { name: 'Movies Only' }).getAttribute('aria-checked')).toBe('false');
  });

  it('turned back on in Trackers, a saved-on row shows on again (the list matches what is searched)', async () => {
    state = state.map(t => (t.id === 4 ? { ...t, enabled: true } : t));     // saved on while off in Trackers
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(rows()).toHaveLength(4));
    expect(screen.getByRole('switch', { name: 'Paused' }).getAttribute('aria-checked')).toBe('false');
    cleanup();
    state = state.map(t => (t.id === 4 ? { ...t, global_enabled: true } : t));
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(rows()).toHaveLength(4));
    const sw = screen.getByRole('switch', { name: 'Paused' });
    expect(sw.getAttribute('aria-checked')).toBe('true');
    expect(sw.getAttribute('aria-disabled')).toBeNull();
  });

  it('desktop rows: titles 15/500, subtitles 13px muted; phone titles 16', async () => {
    render(<SectionTrackersList section="tv" label="Trackers" wide />);
    await waitFor(() => expect(rows()).toHaveLength(4));
    const title = screen.getAllByTestId('section-tracker-name')[0]!;
    expect([title.style.fontSize, title.style.fontWeight]).toEqual(['15px', '500']);
    const sub = screen.getAllByTestId('section-tracker-meta')[0]!;
    expect([sub.style.fontSize, sub.style.color]).toEqual(['13px', 'var(--color-text-muted)']);
    cleanup();
    render(<SectionTrackersList section="tv" label="Trackers" />);
    await waitFor(() => expect(rows()).toHaveLength(4));
    expect(screen.getAllByTestId('section-tracker-name')[0]!.style.fontSize).toBe('16px');
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

  it('shows on luma-test builds for the owner, with the TV tracker list under Options', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/api/settings/sections/tv/trackers')) return server()(url, init);
      if (url.includes('/api/settings/plex')) return res({ owner: true, configured: true });
      if (url.includes('/api/trackers/types')) return res({ types: [] });
      if (url.endsWith('/api/settings/tv')) return res({ paths: [], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6 });
      return res({}, 404);
    }));
    await act(async () => { page('/settings/tv?view=options'); });
    await waitFor(() => expect(screen.getByTestId('tv-upgrades')).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId('section-trackers-tv')).toBeTruthy());
    await waitFor(() => expect(rows()).toHaveLength(4));
  });

  it('the owner nav lists Trackers, in order', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
      const url = String(input);
      if (url.includes('/api/settings/plex')) return res({ owner: true, configured: true });
      if (url.includes('/api/trackers/types')) return res({ types: [] });
      if (url.includes('/api/settings/sections/tv/trackers')) return res({ section: 'tv', trackers: [] });
      return res({}, 404);
    }));
    await act(async () => { page('/settings/tv?view=options'); });   // v325: Calendar (the default) has no Settings nav
    const nav = screen.getByTestId('settings-nav');
    await waitFor(() => expect(Array.from(nav.querySelectorAll('a')).map(a => a.textContent))
      .toEqual(['Home screen', 'Rooms', 'Connectors', 'Notifications', 'Trackers', 'Library paths', 'Movies', 'TV', 'Automation']));
  });

  it('is not offered on production builds', async () => {
    minimalBuild = false;
    await act(async () => { page('/settings/home-screen'); });
    expect(screen.queryByRole('link', { name: 'TV' })).toBeNull();
  });
});
