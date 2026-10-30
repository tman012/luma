/** v256: the Search sections (TV Options: Air-time searches, Slow sweep, On add; Movies: Availability). */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvLibrarySettings } from '@/components/library/TvLibrarySettings';
import { AVAILABILITY_OPTIONS, ON_ADD_OPTIONS } from '@/components/library/SearchSettings';
import { MoviesSettings } from '@/components/movies/MoviesSettings';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const calls: { url: string; method: string; body: unknown }[] = [];
let tv: Record<string, unknown>;
let movies: Record<string, unknown>;
let failPut = false;

beforeEach(() => {
  calls.length = 0;
  failPut = false;
  tv = { paths: [], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6, naming: {},
    automation: { air_searches: true, sweep_enabled: true, on_add: 'search_now', enabled: true } };
  movies = { monitoring: true, preferred_groups: [], picker_profiles: [], trackers: [], qualities: [], profiles: [] };
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u);
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/settings/tv' && method === 'PUT') {
      if (failPut) return res({ errors: { 'automation.on_add': 'bad' } }, 400);
      tv = { ...tv, automation: { ...(tv.automation as object), ...body.automation } };
      return res(tv);
    }
    if (url === '/api/settings/tv') return res(tv);
    if (url === '/api/settings/tv/libraries') return res({ selected: [], libraries: [] });
    if (url === '/api/settings/tv/library') return res({ shows: [], unmatched: [], scan: { running: false, error: null, last_scan: null } });
    if (url.startsWith('/api/settings/movies') && method === 'PUT') { movies = { ...movies, ...body }; return res(movies); }
    if (url.startsWith('/api/settings/movies')) return res(movies);
    return res({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const tvOptions = () => render(<MemoryRouter initialEntries={['/settings/tv/settings']}><TvLibrarySettings wide mode="settings" /></MemoryRouter>);

describe('v256 TV Options > Search', () => {
  it('shows the three rows in sentence case, before Trackers', async () => {
    tvOptions();
    const s = await screen.findByTestId('tv-search-settings');
    expect(within(s).getByText('Search')).toBeTruthy();
    for (const t of ['Air-time searches', 'Slow sweep for missing episodes', 'On add', 'Search now', 'Wait for sweep']) {
      expect(within(s).getByText(t)).toBeTruthy();
    }
    expect(screen.getByTestId('tv-air-searches-toggle').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('tv-sweep-toggle').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('tv-on-add-search_now').getAttribute('aria-checked')).toBe('true');
    expect(ON_ADD_OPTIONS.map(o => o.label)).toEqual(['Search now', 'Wait for sweep']);
  });

  it('toggles save only the changed key', async () => {
    tvOptions();
    fireEvent.click(await screen.findByTestId('tv-air-searches-toggle'));
    await waitFor(() => expect(screen.getByTestId('tv-air-searches-toggle').getAttribute('aria-checked')).toBe('false'));
    fireEvent.click(screen.getByTestId('tv-sweep-toggle'));
    await waitFor(() => expect(screen.getByTestId('tv-sweep-toggle').getAttribute('aria-checked')).toBe('false'));
    const puts = calls.filter(c => c.url === '/api/settings/tv' && c.method === 'PUT').map(c => c.body);
    expect(puts).toEqual([{ automation: { air_searches: false } }, { automation: { sweep_enabled: false } }]);
  });

  it('On add saves Wait for sweep; a refused save puts the old choice back', async () => {
    tvOptions();
    fireEvent.click(await screen.findByTestId('tv-on-add-wait_for_sweep'));
    await waitFor(() => expect(screen.getByTestId('tv-on-add-wait_for_sweep').getAttribute('aria-checked')).toBe('true'));
    expect(calls.find(c => c.method === 'PUT')?.body).toEqual({ automation: { on_add: 'wait_for_sweep' } });
    failPut = true;
    fireEvent.click(screen.getByTestId('tv-on-add-search_now'));
    await waitFor(() => expect(calls.filter(c => c.method === 'PUT')).toHaveLength(2));
    await waitFor(() => expect(screen.getByTestId('tv-on-add-wait_for_sweep').getAttribute('aria-checked')).toBe('true'));
  });

  it('missing automation reads as the defaults', async () => {
    tv = { ...tv, automation: undefined };
    tvOptions();
    await screen.findByTestId('tv-search-settings');
    expect(screen.getByTestId('tv-air-searches-toggle').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('tv-sweep-toggle').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('tv-on-add-search_now').getAttribute('aria-checked')).toBe('true');
  });
});

describe('v256 Movies > Search', () => {
  it('Availability defaults to Released and saves the choice', async () => {
    render(<MoviesSettings />);
    const s = await screen.findByTestId('movie-search-settings');
    expect(within(s).getByText('Availability')).toBeTruthy();
    expect(AVAILABILITY_OPTIONS.map(o => o.label)).toEqual(['Announced', 'In cinemas', 'Released']);
    expect(screen.getByTestId('mv-availability-released').getAttribute('aria-checked')).toBe('true');
    fireEvent.click(screen.getByTestId('mv-availability-in_cinemas'));
    await waitFor(() => expect(screen.getByTestId('mv-availability-in_cinemas').getAttribute('aria-checked')).toBe('true'));
    expect(calls.find(c => c.method === 'PUT')?.body).toEqual({ availability: 'in_cinemas' });
  });

  it('Search sits before Trackers', async () => {
    render(<MoviesSettings />);
    const s = await screen.findByTestId('movie-search-settings');
    const t = screen.getByTestId('movie-trackers');
    expect(s.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

it('no helper text in the Search sections', () => {
  const src = readFileSync(resolve(__dirname, '../src/components/library/SearchSettings.tsx'), 'utf8');
  expect(src).not.toMatch(/hint=|note=|helper/i);
});
