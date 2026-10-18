/** Settings > TV v1 (desktop web): Calendar (default view, the week as an agenda),
 * Search with Add, the Calendar | Import | Libraries | Options tabs in the URL.
 * Times run in America/New_York; "today" is the viewer's local day. */
process.env.TZ = 'America/New_York';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { MemoryRouter, useLocation, useNavigate } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvLibrarySettings } from '@/components/library/TvLibrarySettings';
import { SEARCH_DEBOUNCE_MS } from '@/components/library/TvSearch';
import { CAL_MAX_W } from '@/components/library/TvCalendar';
import {
  addDays, agendaLabel, byDay, episodeState, isDay, localDay, seasonsText, shortDate, weekDays, weekLabel, weekStart, type CalEpisode,
  timeRange,
} from '@/components/library/tvCalendarApi';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const TV = { id: 'p_0000aaaa', name: 'TV', path: '/media/TV', status: 'ok' };
const ANIME = { id: 'p_0000bbbb', name: 'Anime', path: '/media/Anime', status: 'ok' };
// Sat Oct 17, 2026, 11:30 PM in New York (already Oct 18 in UTC).
const NOW = new Date('2026-10-18T03:30:00Z');
const ep = (id: number, air_date: string, downloaded: boolean, show = 'Severance', season = 2, episode = id): CalEpisode => ({
  episode_id: id, series_id: show === 'Severance' ? 1 : 2, show, year: 2022, poster_path: null, season, episode,
  title: `Episode ${episode}`, air_date, downloaded,
});
let paths: typeof TV[];
let episodes: CalEpisode[];
let unmatched: unknown[];
let addStatus: number;
const calls: { url: string; method: string; body: unknown }[] = [];
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'], shouldAdvanceTime: true });
  vi.setSystemTime(NOW);
  calls.length = 0;
  paths = [TV];
  unmatched = [];
  addStatus = 201;
  episodes = [ep(1, '2026-10-12', true), ep(2, '2026-10-16', false), ep(3, '2026-10-17', false),
    ep(4, '2026-10-19', false), ep(5, '2026-10-29', false, 'Dark', 1, 1)];
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u);
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/settings/tv') return res({ paths, upgrades: { mode: 'keep_both' }, rescan_interval_h: 6 });
    if (url === '/api/settings/tv/libraries') return res({ selected: paths.map(p => p.id), libraries: [TV, ANIME].map(l => ({ ...l, selected: paths.some(p => p.id === l.id) })) });
    if (url === '/api/settings/tv/library') return res({ shows: [], unmatched, scan: { running: false, error: null, last_scan: null } });
    if (url.startsWith('/api/settings/tv/calendar')) {
      const p = new URL(url, 'http://x').searchParams;
      const start = p.get('start')!; const end = addDays(start, Number(p.get('days')));
      return res({ start, days: Number(p.get('days')), libraries: paths.length, refreshed_at: null,
        episodes: episodes.filter(e => e.air_date >= start && e.air_date < end) });
    }
    if (url.startsWith('/api/settings/tv/find')) {
      const q = new URL(url, 'http://x').searchParams.get('q');
      if (q === 'nothing') return res({ results: [] });
      return res({ results: [
        { tmdb_id: 95396, title: 'Severance', year: 2022, poster_path: null, seasons: 2, in_library: true, series_id: 1 },
        { tmdb_id: 6001, title: 'Severance Pay', year: 2026, poster_path: '/p.jpg', seasons: 1, in_library: false, series_id: null },
      ] });
    }
    if (url === '/api/settings/tv/shows' && method === 'POST') {
      return addStatus === 201 ? res({ ok: true, series_id: 9 }, 201) : res({ error: 'Couldn\u2019t reach TMDB' }, addStatus);
    }
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

let loc = '';
function Where() { const l = useLocation(); loc = l.pathname + l.search; return null; }
const page = (wide: boolean, at = '/settings/tv') => render(
  <MemoryRouter initialEntries={[at]}><TvLibrarySettings wide={wide} /><Where /></MemoryRouter>,
);
const calCalls = () => calls.filter(c => c.url.startsWith('/api/settings/tv/calendar')).map(c => c.url.replace('/api/settings/tv/calendar?', ''));

// ---------------------------------------------------------------- dates

describe('air times', () => {
  it('a range in the viewer zone, across DST and the meridiem; no airstamp is no time', () => {
    expect(timeRange('2026-10-19T01:00:00Z', 42, 'America/New_York')).toBe('9:00 \u2013 9:42 PM');      // EDT
    expect(timeRange('2026-11-03T02:00:00Z', 42, 'America/New_York')).toBe('9:00 \u2013 9:42 PM');      // EST after Nov 1
    expect(timeRange('2026-10-19T03:30:00Z', 40, 'America/New_York')).toBe('11:30 PM \u2013 12:10 AM');
    expect(timeRange('2026-10-18T20:00:00Z', 60, 'Europe/London')).toBe('9:00 \u2013 10:00 PM');        // BST, a UK network
    expect(timeRange('2026-10-19T01:00:00Z', null, 'America/New_York')).toBe('9:00 PM');
    expect(timeRange(null, 42)).toBeNull();
    expect(timeRange('garbage', 42)).toBeNull();
  });
});

describe('week math', () => {
  it('weeks start on Sunday; labels within and across months and years', () => {
    expect(weekStart('2026-10-17')).toBe('2026-10-11');
    expect(weekStart('2026-10-11')).toBe('2026-10-11');
    expect(weekLabel('2026-10-11')).toBe('Oct 11 \u2013 17');
    expect(weekLabel('2026-12-27')).toBe('Dec 27 \u2013 Jan 2');
    expect(weekLabel('2026-09-27')).toBe('Sep 27 \u2013 Oct 3');
    expect(shortDate('2026-10-20')).toBe('Oct 20');
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDays('2028-02-28', 1)).toBe('2028-02-29');
  });

  it('DST weeks keep 7 whole days (Mar 8 and Nov 1, 2026)', () => {
    expect(weekDays('2026-03-08')).toEqual(['2026-03-08', '2026-03-09', '2026-03-10', '2026-03-11', '2026-03-12', '2026-03-13', '2026-03-14']);
    expect(weekDays('2026-11-01')).toEqual(['2026-11-01', '2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06', '2026-11-07']);
    expect(addDays('2026-03-01', 7)).toBe('2026-03-08');
    expect(addDays('2026-03-08', 7)).toBe('2026-03-15');
    expect(addDays('2026-11-01', -7)).toBe('2026-10-25');
    expect(weekStart('2026-03-08')).toBe('2026-03-08');
    expect(weekStart('2026-11-07')).toBe('2026-11-01');
    expect(localDay(new Date(2026, 2, 8, 1, 59))).toBe('2026-03-08');
    expect(localDay(new Date(2026, 2, 8, 3, 0))).toBe('2026-03-08');
    expect(localDay(new Date(2026, 10, 1, 1, 30))).toBe('2026-11-01');
    expect(localDay(new Date(2026, 10, 1, 23, 59))).toBe('2026-11-01');
  });

  it('today is the local day, not UTC', () => {
    expect(NOW.toISOString().slice(0, 10)).toBe('2026-10-18');
    expect(localDay(NOW)).toBe('2026-10-17');
    expect(localDay()).toBe('2026-10-17');
    expect(isDay('2026-02-30')).toBe(false);
    expect(isDay('2026-10-11')).toBe(true);
    expect(isDay('x')).toBe(false);
  });

  it('states: downloaded with a file; missing before today; upcoming from today', () => {
    const t = '2026-10-17';
    expect(episodeState({ downloaded: true, air_date: '2026-10-20' }, t)).toBe('downloaded');
    expect(episodeState({ downloaded: false, air_date: '2026-10-16' }, t)).toBe('missing');
    expect(episodeState({ downloaded: false, air_date: '2026-10-17' }, t)).toBe('upcoming');
    expect(episodeState({ downloaded: false, air_date: null }, t)).toBe('upcoming');
  });

  it('agenda labels and grouping', () => {
    expect(agendaLabel('2026-10-17', '2026-10-17')).toBe('Today');
    expect(agendaLabel('2026-10-18', '2026-10-17')).toBe('Tomorrow');
    expect(agendaLabel('2026-10-20', '2026-10-17')).toBe('Tue, Oct 20');
    expect(byDay([ep(2, '2026-10-20', false), ep(1, '2026-10-18', false), ep(3, '2026-10-20', false)]).map(([d, l]) => [d, l.length]))
      .toEqual([['2026-10-18', 1], ['2026-10-20', 2]]);
    expect(seasonsText(5)).toBe('5 seasons');
    expect(seasonsText(1)).toBe('1 season');
  });
});

// ---------------------------------------------------------------- calendar

describe('Calendar', () => {
  it('opens by default: this week as a 7-column grid, Sunday first, with states and air dates; no Today on this week', async () => {
    page(true);
    const rows = await screen.findAllByTestId('tv-agenda-row');
    expect(screen.getByTestId('tv-tab-calendar').getAttribute('aria-selected')).toBe('true');
    expect(calCalls()).toEqual(['start=2026-10-11&days=7']);
    expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 11 \u2013 17');
    expect(screen.queryByTestId('tv-cal-today')).toBeNull();
    expect(screen.getAllByTestId('tv-grid-day').map(d => d.firstElementChild!.textContent)).toEqual(
      ['Sun 10/11', 'Mon 10/12', 'Tue 10/13', 'Wed 10/14', 'Thu 10/15', 'Fri 10/16', 'Sat 10/17']);
    expect(rows.map(r => r.getAttribute('aria-label'))).toEqual([
      'Downloaded: Severance S02E01', 'Missing: Severance S02E02', 'Upcoming: Severance S02E03']);
    expect(rows.map(r => within(r).getByTestId('tv-stripe').style.background)).toEqual(['rgb(34, 197, 94)', 'rgb(239, 68, 68)', 'rgb(59, 130, 246)']);
    expect(within(rows[0]!).getByTestId('tv-check')).toBeTruthy();
    expect(within(rows[1]!).queryByTestId('tv-check')).toBeNull();
    expect(rows[0]!.textContent).toContain('Episode 1');
    // rows aren't clickable
    expect(rows.every(r => r.tagName === 'DIV' && !r.querySelector('button, a'))).toBe(true);
  });

  it('week navigation, Today, and an empty week; the week sits in the URL', async () => {
    page(true);
    await screen.findAllByTestId('tv-agenda-row');
    fireEvent.click(screen.getByTestId('tv-cal-next'));
    await waitFor(() => expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 18 \u2013 24'));
    await screen.findAllByTestId('tv-agenda-row');
    expect(loc).toBe('/settings/tv?week=2026-10-18');
    fireEvent.click(screen.getByTestId('tv-cal-next'));
    fireEvent.click(screen.getByTestId('tv-cal-next'));
    expect((await screen.findByTestId('tv-cal-empty')).textContent).toBe('No episodes this week');
    expect(screen.getByTestId('tv-cal-label').textContent).toBe('Nov 1 \u2013 7');
    fireEvent.click(screen.getByTestId('tv-cal-prev'));
    await waitFor(() => expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 25 \u2013 31'));
    await screen.findAllByTestId('tv-agenda-row');                         // Dark, Oct 29
    fireEvent.click(screen.getByTestId('tv-cal-today'));
    await waitFor(() => expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 11 \u2013 17'));
    expect(loc).toBe('/settings/tv');
    expect(calCalls()).toEqual(['start=2026-10-11&days=7', 'start=2026-10-18&days=7', 'start=2026-10-25&days=7',
      'start=2026-11-01&days=7', 'start=2026-10-25&days=7', 'start=2026-10-11&days=7']);
  });

  it('a week across the DST change opens on its Sunday (Nov 1)', async () => {
    page(true, '/settings/tv?week=2026-11-04');
    await screen.findByTestId('tv-cal-empty');
    expect(calCalls()).toEqual(['start=2026-11-01&days=7']);
    expect(screen.getByTestId('tv-cal-label').textContent).toBe('Nov 1 \u2013 7');
  });

  it('quiet loading: glass .04 placeholder rows, no spinner, no motion', async () => {
    let release!: () => void;
    const fetch0 = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn(async (u: string, i?: RequestInit) => {
      if (String(u).startsWith('/api/settings/tv/calendar')) await new Promise<void>(r => { release = r; });
      return fetch0(u, i);
    }));
    page(true);
    const ph = await screen.findByTestId('tv-cal-loading');
    expect((ph.firstElementChild as HTMLElement).style.background).toBe('rgba(255, 255, 255, 0.04)');
    expect(ph.firstElementChild!.children).toHaveLength(3);
    expect(document.querySelector('.tvi-spin, .tvc-pulse, [class*="spin"]')).toBeNull();
    expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 11 \u2013 17');   // the toolbar stays put
    await act(async () => release());
    await screen.findAllByTestId('tv-agenda-row');
  });
});

// ---------------------------------------------------------------- search

describe('Search', () => {
  const type = (v: string) => fireEvent.change(screen.getByTestId('tv-search'), { target: { value: v } });

  it('a 44px field; waits 250ms and 2 characters; library first with In library (not clickable), then TMDB with Add', async () => {
    page(true);
    await screen.findAllByTestId('tv-agenda-row');
    const field = screen.getByTestId('tv-search') as HTMLInputElement;
    expect([field.placeholder, field.style.height]).toEqual(['Search shows', '44px']);
    type('S');
    expect(screen.getByTestId('tv-section').getAttribute('data-view')).toBe('calendar');
    type('Se'); type('Sev');
    expect(SEARCH_DEBOUNCE_MS).toBe(250);
    const rows = await screen.findAllByTestId('tv-search-row');
    expect(calls.filter(c => c.url.startsWith('/api/settings/tv/find')).map(c => c.url)).toEqual(['/api/settings/tv/find?q=Sev']);
    expect(rows.map(r => r.textContent)).toEqual(['Severance (2022)2 seasonsIn library', 'Severance Pay (2026)1 seasonAdd']);
    expect(rows[0]!.getAttribute('data-in-library')).toBe('true');
    expect(rows[0]!.querySelector('button, a')).toBeNull();
    expect(screen.getByTestId('tv-search-results').style.maxWidth).toBe('720px');
    fireEvent.keyDown(screen.getByTestId('tv-search'), { key: 'Escape' });
    await screen.findAllByTestId('tv-agenda-row');                          // back to the view it replaced
  });

  it('no results says No results; the x clears', async () => {
    page(true);
    await screen.findByTestId('tv-search');
    type('nothing');
    expect((await screen.findByTestId('tv-search-empty')).textContent).toBe('No results');
    fireEvent.click(screen.getByTestId('tv-search-clear'));
    expect((screen.getByTestId('tv-search') as HTMLInputElement).value).toBe('');
    await screen.findAllByTestId('tv-agenda-row');
  });

  it('Add with one library adds at once, then shows In library', async () => {
    page(true);
    await screen.findByTestId('tv-search');
    type('Severance');
    fireEvent.click(await screen.findByTestId('tv-add'));
    await waitFor(() => expect(screen.getAllByTestId('tv-in-library')).toHaveLength(2));
    expect(calls.filter(c => c.method === 'POST').map(c => [c.url, c.body])).toEqual([['/api/settings/tv/shows', { tmdb_id: 6001 }]]);
    expect(screen.queryByTestId('tv-add-picker')).toBeNull();
  });

  it('Add with 2+ libraries asks where: nothing preselected, Add at 40% until one is picked; Cancel adds nothing', async () => {
    paths = [TV, ANIME];
    page(true);
    await screen.findByTestId('tv-search');
    type('Severance');
    fireEvent.click(await screen.findByTestId('tv-add'));
    fireEvent.click(screen.getByTestId('tv-add-cancel'));
    expect(screen.queryByTestId('tv-add-picker')).toBeNull();
    expect(calls.some(c => c.method === 'POST')).toBe(false);
    fireEvent.click(screen.getByTestId('tv-add'));
    const picker = screen.getByTestId('tv-add-picker');
    expect(picker.style.width).toBe('320px');
    expect(within(picker).getAllByTestId('tv-add-path').map(r => [r.textContent, r.getAttribute('aria-checked')]))
      .toEqual([['TV/media/TV', 'false'], ['Anime/media/Anime', 'false']]);
    const add = within(picker).getByTestId('tv-add-confirm') as HTMLButtonElement;
    expect([add.disabled, add.style.opacity]).toEqual([true, '0.4']);
    fireEvent.click(within(picker).getAllByTestId('tv-add-path')[1]!);
    expect([add.disabled, add.style.opacity]).toEqual([false, '1']);
    fireEvent.click(add);
    await waitFor(() => expect(screen.getAllByTestId('tv-in-library')).toHaveLength(2));
    expect(calls.find(c => c.method === 'POST')?.body).toEqual({ tmdb_id: 6001, path_id: ANIME.id });
  });

  it("a failed Add says Couldn't add show with Retry", async () => {
    addStatus = 502;
    page(true);
    await screen.findByTestId('tv-search');
    type('Severance');
    fireEvent.click(await screen.findByTestId('tv-add'));
    const err = await screen.findByTestId('tv-add-error');
    expect(err.textContent).toBe("Couldn't add show" + 'Retry');
    expect(err.style.color).toBe('rgb(239, 68, 68)');
    addStatus = 201;
    fireEvent.click(within(err).getByTestId('tv-add-retry'));
    await waitFor(() => expect(screen.queryByTestId('tv-add-error')).toBeNull());
    await waitFor(() => expect(screen.getAllByTestId('tv-in-library')).toHaveLength(2));
  });
});

// ---------------------------------------------------------------- frame

describe('tabs', () => {
  it('Calendar | Import | Libraries | Options in the URL, so Back works; Options holds Upgrades; no Import badge', async () => {
    unmatched = [{ id: 7, path_id: TV.id, path_name: 'TV', folder: 'Mystery', guess_title: null, guess_year: null, candidates: [], reason: null, tier: 'none' }];
    page(true);
    await screen.findAllByTestId('tv-agenda-row');
    expect(screen.getByTestId('tv-title').textContent).toBe('TV');
    expect(screen.getAllByRole('tab').slice(0, 4).map(t => t.textContent)).toEqual(['Calendar', 'Import', 'Libraries', 'Options']);
    expect(screen.queryByTestId('tv-import-badge')).toBeNull();
    fireEvent.click(screen.getByTestId('tv-tab-options'));
    await screen.findByTestId('tv-upgrades');
    expect(loc).toBe('/settings/tv?view=options');
    fireEvent.click(screen.getByTestId('tv-tab-libraries'));
    expect(loc).toBe('/settings/tv?view=libraries');
    expect(screen.getByTestId('tv-paths').closest('div[style*="display: none"]')).toBeNull();
    fireEvent.click(screen.getByTestId('tv-tab-import'));
    await screen.findByTestId('tv-import-start');
    expect(screen.getByTestId('tv-paths').parentElement!.style.display).toBe('none');
  });

  it('Back walks the tabs (history)', async () => {
    function Back() { const nav = useNavigate(); return <button type="button" data-testid="back" onClick={() => nav(-1)} />; }
    render(<MemoryRouter initialEntries={['/settings/tv']}><TvLibrarySettings wide /><Where /><Back /></MemoryRouter>);
    await screen.findAllByTestId('tv-agenda-row');
    fireEvent.click(screen.getByTestId('tv-tab-options'));
    fireEvent.click(screen.getByTestId('tv-tab-import'));
    expect(loc).toBe('/settings/tv?view=import');
    fireEvent.click(screen.getByTestId('back'));
    await waitFor(() => expect(screen.getByTestId('tv-tab-options').getAttribute('aria-selected')).toBe('true'));
    fireEvent.click(screen.getByTestId('back'));
    await waitFor(() => expect(screen.getByTestId('tv-tab-calendar').getAttribute('aria-selected')).toBe('true'));
    expect(loc).toBe('/settings/tv');
  });

  it('No TV libraries: Calendar and Search offer Choose libraries, which opens Libraries', async () => {
    paths = [];
    page(true);
    expect((await screen.findByTestId('tv-no-libraries')).textContent).toBe('No TV libraries' + 'Choose libraries');
    expect(calCalls()).toEqual([]);
    fireEvent.change(screen.getByTestId('tv-search'), { target: { value: 'Severance' } });
    expect(screen.getByTestId('tv-no-libraries')).toBeTruthy();
    fireEvent.click(screen.getByTestId('tv-choose-libraries'));
    expect(loc).toBe('/settings/tv?view=libraries');
    expect((screen.getByTestId('tv-search') as HTMLInputElement).value).toBe('');
  });

  it('no helper text, American English, sentence case', () => {
    const src = ['TvLibrarySettings.tsx', 'TvCalendar.tsx', 'TvSearch.tsx', 'tvCalendarApi.ts']
      .map(f => readFileSync(resolve(__dirname, '../src/components/library/' + f), 'utf8')).join('\n');
    expect(src).not.toMatch(/hint=|note=|helper=/i);
    expect(src).not.toMatch(/colour|favourite|catalogue|cancelled/i);
    for (const label of ['Search shows', 'No episodes this week', 'Upcoming', 'Downloaded', 'Missing', 'In library', 'Add to',
      "Couldn't add show", 'No results', 'Wrong show?', 'No TV libraries', 'Choose libraries', 'Today', 'Tomorrow'])
      expect(src).toContain(label);
  });
});
