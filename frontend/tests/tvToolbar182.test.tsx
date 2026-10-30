/** v182 (Taylor, a bug on live v181): the TV toolbar search on every TV view finds only your own shows (tv.db,
 * already loaded): no network search, no Add buttons; a result opens the series detail page. */
process.env.TZ = 'America/New_York';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TV_VIEWS, TvLibrarySettings } from '@/components/library/TvLibrarySettings';
import { SEARCH_DEBOUNCE_MS } from '@/components/library/TvSearch';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const TV = { id: 'p_0000aaaa', name: 'TV', path: '/media/TV', status: 'ok' };
const SHOW = { id: 1, title: 'Severance', year: 2022, tmdb_id: 95396, path_id: TV.id, path_name: 'TV', folder: 'Severance (2022)',
  poster_path: null, monitored: true, have: 5, aired: 12, episodes: 12, seasons: 2 };
const calls: string[] = [];
let loc = '';
function Where() { const l = useLocation(); loc = l.pathname + l.search; return null; }
beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u);
    calls.push(url);
    if (url === '/api/settings/tv') return res({ paths: [TV], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6 });
    if (url === '/api/settings/tv/libraries') return res({ selected: [TV.id], libraries: [{ ...TV, selected: true }] });
    if (url === '/api/settings/tv/library') return res({ shows: [SHOW], unmatched: [], scan: { running: false, error: null, last_scan: null } });
    if (url.startsWith('/api/settings/tv/find')) return res({ results: [{ tmdb_id: 7, title: 'Severance Pay', year: 2026, in_library: false }] });
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const NETWORK_SEARCH = /\/find|search|lookup|tmdb|tvdb|themoviedb/i;
// v326 (Taylor): the desktop calendar has no search box; every other view keeps it
const views = [...TV_VIEWS.filter(v => v.id !== 'calendar').map(v => `/settings/tv?view=${v.id}`), '/settings/tv?view=series&show=1'];

describe('the TV toolbar search (v182)', () => {
  it.each(views)('%s: finds only your shows, with no network search and no Add; a result opens the detail page', async at => {
    render(<MemoryRouter initialEntries={[at]}><TvLibrarySettings wide /><Where /></MemoryRouter>);
    const field = await screen.findByTestId('tv-search') as HTMLInputElement;
    expect(field.placeholder).toBe('Search your shows');
    await waitFor(() => expect(calls).toContain('/api/settings/tv/library'));
    await act(async () => { await new Promise(r => setTimeout(r, 50)); });
    const before = calls.length;
    for (const v of ['S', 'Se', 'Sev', 'Severance']) fireEvent.change(field, { target: { value: v } });
    await act(async () => { await new Promise(r => setTimeout(r, SEARCH_DEBOUNCE_MS + 100)); });
    const after = calls.slice(before);
    expect(after.filter(u => NETWORK_SEARCH.test(u))).toEqual([]);
    expect(calls.filter(u => u.startsWith('/api/settings/tv/find'))).toEqual([]);
    const rows = screen.getAllByTestId('tv-owned-row');
    expect(rows.map(r => r.getAttribute('data-show-id'))).toEqual(['1']);
    expect(screen.queryAllByTestId('tv-add')).toHaveLength(0);
    expect(screen.queryAllByTestId('tv-search-row')).toHaveLength(0);
    fireEvent.click(rows[0]!);
    await waitFor(() => expect(loc).toBe('/settings/tv?view=series&show=1'));
  });

  it.each(['/settings/tv', '/settings/tv?view=calendar'])('%s (desktop calendar): no search box (v326)', async at => {
    render(<MemoryRouter initialEntries={[at]}><TvLibrarySettings wide /><Where /></MemoryRouter>);
    await screen.findByTestId('tv-tabs');
    await waitFor(() => expect(calls).toContain('/api/settings/tv/library'));
    expect(screen.queryByTestId('tv-search')).toBeNull();
    expect(screen.queryByTestId('tv-search-box')).toBeNull();
  });

  it('Add series lives only on the Series page, in its own dialog', async () => {
    render(<MemoryRouter initialEntries={['/settings/tv?view=series']}><TvLibrarySettings wide /><Where /></MemoryRouter>);
    fireEvent.click(await screen.findByTestId('tv-add-series'));
    expect(screen.getByTestId('tv-add-series-modal').getAttribute('aria-label')).toBe('Add series');
    cleanup();
    for (const v of TV_VIEWS.filter(x => x.id !== 'series')) {
      render(<MemoryRouter initialEntries={[`/settings/tv?view=${v.id}`]}><TvLibrarySettings wide /></MemoryRouter>);
      await screen.findByTestId(v.id === 'calendar' ? 'tv-tabs' : 'tv-search');
      expect(screen.queryByTestId('tv-add-series')).toBeNull();
      cleanup();
    }
  });
});
