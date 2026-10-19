/** v181: the Series list view bulk editor (checkboxes, select all, the action bar, the confirmation),
 * the show sheet episode select, and the calendar toolbar search that finds only your own shows. */
process.env.TZ = 'America/New_York';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvSeries } from '@/components/library/TvSeries';
import { TvLibrarySettings } from '@/components/library/TvLibrarySettings';
import { ShowSheet, airText, changeLines, draftChanges, seasonOrder } from '@/components/library/TvBulk';
import { ownedMatches } from '@/components/library/TvSearch';
import type { Show } from '@/components/library/tvLibraryApi';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const TV = { id: 'p_0000aaaa', name: 'TV', path: '/media/TV', status: 'ok' };
const show = (id: number, title: string, extra: Partial<Show> = {}): Show => ({
  id, tmdb_id: 1000 + id, title, year: 2020, path_id: TV.id, path_name: 'TV', folder: title, poster_path: null, monitored: true,
  files: 1, episodes: 10, linked: 1, mismatch: 0, unrecognized: 0, aired: 10, have: 4, continuing: true,
  monitor: 'all', monitor_new_seasons: 'all', profile_id: 1, series_type: 'standard', season_folder: true, ...extra,
});
const SHOWS = [show(1, 'Severance'), show(2, 'The Bear', { monitored: false, monitor: 'none', profile_id: 2 }), show(3, 'Dark', { series_type: 'anime' }),
  show(4, 'Slow Horses')];
const PROFILES = [{ id: 1, name: 'HD-1080p', items: [], cutoff: null, upgrades: false, default: true }, { id: 2, name: 'Ultra-HD', items: [], cutoff: null, upgrades: false, default: false }];
const VIEW = {
  id: 1, title: 'Severance', year: 2022, tmdb_id: 95396, monitored: true, monitor: 'all', monitor_new_seasons: 'all', monitor_specials: false,
  season_folder: true, series_type: 'standard', profile_id: 1, path_id: TV.id, folder: 'Severance', path: '/media/TV/Severance',
  seasons: [{ season: 1, monitored: true }, { season: 2, monitored: true }],
  episodes: [
    { id: 11, season: 1, episode: 1, monitored: true, title: 'Good News About Hell', air_date: '2022-02-18', has_file: true },
    { id: 12, season: 1, episode: 2, monitored: true, title: 'Half Loop', air_date: '2022-02-18', has_file: false },
    { id: 21, season: 2, episode: 1, monitored: false, title: 'Hello, Ms. Cobel', air_date: '2025-01-17', has_file: false },
    { id: 22, season: 2, episode: 2, monitored: false, title: 'Goodbye, Mrs. Selvig', air_date: '2025-01-24', has_file: false },
  ],
};
const calls: { url: string; method: string; body: unknown }[] = [];
let bulkStatus = 200;
beforeEach(() => {
  calls.length = 0; bulkStatus = 200;
  localStorage.clear();
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u); const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/settings/tv') return res({ paths: [TV], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6 });
    if (url === '/api/settings/tv/libraries') return res({ selected: [TV.id], libraries: [{ ...TV, selected: true }] });
    if (url === '/api/settings/tv/library') return res({ shows: SHOWS, unmatched: [], scan: { running: false, error: null, last_scan: null } });
    if (url.startsWith('/api/settings/tv/calendar')) return res({ start: '2026-10-18', days: 7, libraries: 1, refreshed_at: null, episodes: [] });
    if (url === '/api/settings/tv/profiles') return res({ profiles: PROFILES, qualities: [] });
    if (url === '/api/settings/tv/shows/bulk' && method === 'PUT') {
      return bulkStatus === 200 ? res({ ok: true, updated: body.ids.length, not_found: [] }) : res({ error: 'Invalid' }, bulkStatus);
    }
    if (url === '/api/settings/tv/shows/1/episodes/monitored' && method === 'PUT') {
      const ids = new Set(body.episode_ids as number[]);
      return res({ ...VIEW, updated: ids.size, episodes: VIEW.episodes.map(e => (ids.has(e.id) ? { ...e, monitored: body.monitored } : e)) });
    }
    if (url === '/api/settings/tv/shows/1') return res(VIEW);
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const searchy = (c: { url: string }) => /\/(find|search|grab|interactive|tvdb|tmdb)/.test(c.url) || c.url.includes('/search');
const pickOption = (selectTestid: string, label: string) => {
  fireEvent.click(screen.getByTestId(selectTestid));
  fireEvent.click(within(screen.getByTestId(selectTestid.replace(/-select$/, '-menu'))).getByRole('option', { name: label }));
};

describe('Series list view', () => {
  it('a Posters/List toggle; the list has a checkbox per show, select all and a selected count', async () => {
    const onChanged = vi.fn();
    render(<TvSeries shows={SHOWS} onChanged={onChanged} />);
    expect(screen.getAllByTestId('tv-series-tile')).toHaveLength(4);
    fireEvent.click(screen.getByTestId('tv-series-layout-list'));
    expect(localStorage.getItem('luma.tv.series.layout')).toBe('list');
    const rows = screen.getAllByTestId('tv-list-row');
    expect(rows.map(r => within(r).getByTestId('tv-list-open').textContent)).toEqual(['The Bear (2020)', 'Dark (2020)', 'Severance (2020)', 'Slow Horses (2020)']);
    await waitFor(() => expect(within(rows[0]!).getByText('Ultra-HD')).toBeTruthy());
    expect(within(rows[0]!).getByTestId('tv-list-monitored').textContent).toBe('Unmonitored');
    expect(within(rows[1]!).getByText('Anime')).toBeTruthy();
    expect(screen.getByTestId('tv-series-picked').textContent).toBe('0 selected');
    expect(screen.queryByTestId('tv-bulk-bar')).toBeNull();
    fireEvent.click(within(rows[1]!).getByTestId('tv-list-check'));
    fireEvent.click(rows[2]!);                                                       // the whole row toggles
    expect(screen.getByTestId('tv-series-picked').textContent).toBe('2 selected');
    expect(screen.getByTestId('tv-list-select-all').getAttribute('aria-checked')).toBe('mixed');
    fireEvent.click(screen.getByTestId('tv-list-select-all'));
    expect(screen.getByTestId('tv-bulk-count').textContent).toBe('4 selected');
    expect(screen.getByTestId('tv-list-select-all').getAttribute('aria-checked')).toBe('true');
    fireEvent.click(screen.getByTestId('tv-list-select-all'));
    expect(screen.queryByTestId('tv-bulk-bar')).toBeNull();
  });

  it('the action bar offers the 6 fields (no root folder, no delete); Monitor lists all 9 options', async () => {
    render(<TvSeries shows={SHOWS} />);
    fireEvent.click(screen.getByTestId('tv-series-layout-list'));
    fireEvent.click(screen.getByTestId('tv-list-select-all'));
    const bar = screen.getByTestId('tv-bulk-bar');
    const labels = [...bar.querySelectorAll('span[id$="-label"]')].map(s => s.textContent);
    expect(labels).toEqual(['Monitored', 'Monitor', 'Monitor new seasons', 'Quality profile', 'Series type', 'Season folder']);
    expect(bar.textContent).not.toMatch(/root|path|delete/i);
    expect((screen.getByTestId('tv-bulk-apply') as HTMLButtonElement).disabled).toBe(true);     // nothing to change yet
    fireEvent.click(screen.getByTestId('tvb-monitor-select'));
    const menu = screen.getByTestId('tvb-monitor-menu');
    expect(menu.style.bottom).toBe('44px');                                                     // opens upward
    expect(within(menu).getAllByRole('option').map(o => o.textContent)).toEqual(['No change', 'All episodes', 'Future episodes', 'Missing episodes',
      'Existing episodes', 'Recent episodes', 'Pilot episode', 'First season', 'Last season', 'None']);
    await waitFor(() => expect(calls.some(c => c.url === '/api/settings/tv/profiles')).toBe(true));
    fireEvent.click(screen.getByTestId('tvb-profile-select'));
    expect(within(screen.getByTestId('tvb-profile-menu')).getAllByRole('option').map(o => o.textContent)).toEqual(['No change', 'HD-1080p', 'Ultra-HD']);
  });

  it('more than 1 show asks first (with the count and the changes); Apply sends only the changed fields and never searches', async () => {
    const onChanged = vi.fn();
    render(<TvSeries shows={SHOWS} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId('tv-series-layout-list'));
    await waitFor(() => expect(calls.some(c => c.url === '/api/settings/tv/profiles')).toBe(true));
    fireEvent.click(screen.getByTestId('tv-list-select-all'));
    pickOption('tvb-monitor-select', 'All episodes');
    pickOption('tvb-profile-select', 'Ultra-HD');
    pickOption('tvb-season-folder-select', 'No');
    fireEvent.click(screen.getByTestId('tv-bulk-apply'));
    const dlg = screen.getByTestId('tv-bulk-confirm');
    expect(within(dlg).getByRole('heading').textContent).toBe('Apply to 4 shows?');
    expect(screen.getByTestId('tv-bulk-confirm-changes').textContent).toBe('MonitorAll episodesQuality profileUltra-HDSeason folderNo');
    expect(calls.some(c => c.method === 'PUT')).toBe(false);
    fireEvent.click(screen.getByTestId('tv-bulk-confirm-cancel'));
    expect(calls.some(c => c.method === 'PUT')).toBe(false);
    fireEvent.click(screen.getByTestId('tv-bulk-apply'));
    fireEvent.click(screen.getByTestId('tv-bulk-confirm-apply'));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const puts = calls.filter(c => c.method !== 'GET');
    expect(puts).toEqual([{ url: '/api/settings/tv/shows/bulk', method: 'PUT', body: { ids: [2, 3, 1, 4], changes: { monitor: 'all', profile_id: 2, season_folder: false } } }]);
    expect(calls.filter(searchy)).toEqual([]);
    expect(screen.queryByTestId('tv-bulk-bar')).toBeNull();                                     // the selection clears
  });

  it('one show applies without asking; a failed save says so and keeps the selection', async () => {
    render(<TvSeries shows={SHOWS} />);
    fireEvent.click(screen.getByTestId('tv-series-layout-list'));
    fireEvent.click(screen.getAllByTestId('tv-list-check')[0]!);
    pickOption('tvb-monitored-select', 'On');
    bulkStatus = 400;
    fireEvent.click(screen.getByTestId('tv-bulk-apply'));
    expect(screen.queryByTestId('tv-bulk-confirm')).toBeNull();
    expect((await screen.findByTestId('tv-bulk-error')).textContent).toBe("Couldn't save");
    expect(calls.filter(c => c.method === 'PUT').map(c => c.body)).toEqual([{ ids: [2], changes: { monitored: true } }]);
    expect(screen.getByTestId('tv-bulk-count').textContent).toBe('1 selected');
  });

  it('the draft maps to the API shape and reads back in words', () => {
    const c = draftChanges({ monitored: 'off', monitor: 'last_season', monitor_new_seasons: 'none', profile_id: 1, series_type: 'daily', season_folder: 'yes' });
    expect(c).toEqual({ monitored: false, monitor: 'last_season', monitor_new_seasons: 'none', profile_id: 1, series_type: 'daily', season_folder: true });
    expect(changeLines(c, PROFILES).map(l => l.join(': '))).toEqual(['Monitored: Off', 'Monitor: Last season', 'Monitor new seasons: None',
      'Quality profile: HD-1080p', 'Series type: Daily', 'Season folder: Yes']);
  });
});

describe('Episode select', () => {
  it('seasons newest first; per-episode, per-season and select all; Monitor and Unmonitor send only the picked ids', async () => {
    const onChanged = vi.fn();
    render(<ShowSheet seriesId={1} title="Severance" onClose={() => {}} onChanged={onChanged} />);
    const seasons = await screen.findAllByTestId('tv-ep-season');
    expect(seasons.map(s => s.getAttribute('data-season'))).toEqual(['2', '1']);
    expect(within(seasons[0]!).getByTestId('tv-ep-season-monitored').textContent).toBe('0 of 2 monitored');
    const rows = screen.getAllByTestId('tv-ep-row');
    expect(rows[0]!.textContent).toBe('1Hello, Ms. CobelJan 17, 2025Unmonitored');
    expect((screen.getByTestId('tv-ep-monitor') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(seasons[0]!).getByTestId('tv-ep-season-check'));
    expect(screen.getByTestId('tv-ep-count').textContent).toBe('2 selected');
    expect(screen.getByTestId('tv-ep-select-all').getAttribute('aria-checked')).toBe('mixed');
    fireEvent.click(screen.getByTestId('tv-ep-monitor'));
    await waitFor(() => expect(within(screen.getAllByTestId('tv-ep-season')[0]!).getByTestId('tv-ep-season-monitored').textContent).toBe('2 of 2 monitored'));
    expect(screen.getByTestId('tv-ep-count').textContent).toBe('0 selected');
    fireEvent.click(screen.getByTestId('tv-ep-select-all'));
    expect(screen.getByTestId('tv-ep-count').textContent).toBe('4 selected');
    fireEvent.click(screen.getAllByTestId('tv-ep-check')[3]!);                                   // un-pick one
    fireEvent.click(screen.getByTestId('tv-ep-unmonitor'));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
    expect(calls.filter(c => c.method === 'PUT').map(c => [c.url, c.body])).toEqual([
      ['/api/settings/tv/shows/1/episodes/monitored', { episode_ids: [21, 22], monitored: true }],
      ['/api/settings/tv/shows/1/episodes/monitored', { episode_ids: [11, 21, 22], monitored: false }],
    ]);
    expect(calls.filter(searchy)).toEqual([]);
  });

  it('helpers: air dates without a zone shift, specials last', () => {
    expect(airText('2025-01-01')).toBe('Jan 1, 2025');
    expect(airText(null)).toBe('');
    expect(seasonOrder([{ season: 0 }, { season: 1 }, { season: 3 }] as never)).toEqual([3, 1, 0]);
  });
});

describe('Calendar toolbar search', () => {
  it('finds only your own shows (no TMDB or TVDB lookup); a result opens the show page', async () => {
    render(<MemoryRouter initialEntries={['/settings/tv']}><TvLibrarySettings wide /></MemoryRouter>);
    const field = await screen.findByTestId('tv-search') as HTMLInputElement;
    expect(field.placeholder).toBe('Search your shows');
    await waitFor(() => expect(calls.some(c => c.url === '/api/settings/tv/library')).toBe(true));
    fireEvent.change(field, { target: { value: 'se' } });
    const rows = await screen.findAllByTestId('tv-owned-row');
    expect(rows.map(r => r.getAttribute('data-show-id'))).toEqual(['1', '4']);                 // Severance, Slow Horses (hor-SE-s)
    expect(screen.queryByTestId('tv-add')).toBeNull();
    fireEvent.change(field, { target: { value: 'Severance Pay' } });
    expect((await screen.findByTestId('tv-search-empty')).textContent).toBe('No shows');      // unowned: nothing
    fireEvent.change(field, { target: { value: 'bear' } });
    fireEvent.keyDown(field, { key: 'ArrowDown' });
    fireEvent.keyDown(field, { key: 'Enter' });
    // v182: a result opens the show's detail page
    await waitFor(() => expect(calls.some(c => c.url === '/api/settings/tv/shows/2/detail')).toBe(true));
    expect(document.querySelector('[data-testid^="tv-detail"]')).toBeTruthy();
    expect(field.value).toBe('');
    await new Promise(r => setTimeout(r, 300));                                                // past the debounce
    expect(calls.filter(c => /\/find|\/search|tmdb|tvdb/i.test(c.url))).toEqual([]);
  });

  it('ranks exact and prefix matches first, ignores a leading The and punctuation', () => {
    const s = [show(1, 'The Office'), show(2, 'Office Space'), show(3, 'Back Office'), show(4, 'S.W.A.T.')];
    expect(ownedMatches(s, 'office').map(x => x.id)).toEqual([1, 2, 3]);
    expect(ownedMatches(s, 'swat').map(x => x.id)).toEqual([4]);
    expect(ownedMatches(s, 'o')).toEqual([]);
  });
});
