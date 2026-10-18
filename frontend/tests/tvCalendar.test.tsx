/** Settings > TV: Calendar (default view), Search with Add, Show detail, the
 * Calendar | Import | Libraries | Options tabs in the URL (docs/design/tv.md).
 * Times run in America/New_York; "today" is the viewer's local day. */
process.env.TZ = 'America/New_York';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { MemoryRouter, useLocation, useNavigate } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvLibrarySettings } from '@/components/library/TvLibrarySettings';
import { HIGHLIGHT_MS } from '@/components/library/TvShowDetail';
import { SEARCH_DEBOUNCE_MS } from '@/components/library/TvSearch';
import {
  addDays, agendaLabel, byDay, columnLabel, episodeState, haveText, isDay, localDay, longDate, metaText, openingSeason,
  seasonsText, weekDays, weekLabel, weekStart, type CalEpisode,
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
const SEV_DETAIL = {
  id: 1, tmdb_id: 95396, title: 'Severance', year: 2022, network: 'Apple TV+', status: 'Returning Series', poster_path: null,
  path_id: TV.id, path_name: 'TV', folder: 'Severance (2022)', have: 3, aired: 5,
  seasons: [
    { number: 1, name: 'Season 1', episodes: [{ id: 11, season: 1, episode: 1, title: 'Good News About Hell', air_date: '2022-02-18', downloaded: true, files: ['a.mkv'] }] },
    { number: 2, name: 'Season 2', episodes: [3, 4, 5].map(e => ({ id: 20 + e, season: 2, episode: e, title: `Episode ${e}`, air_date: `2026-10-${4 * e}`, downloaded: e === 3, files: [] })) },
    { number: 0, name: 'Specials', episodes: [{ id: 99, season: 0, episode: 1, title: 'Main Title', air_date: null, downloaded: false, files: [] }] },
  ],
};

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
    if (url === '/api/settings/tv/shows/1') return res(SEV_DETAIL);
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

describe('week math', () => {
  it('weeks start on Sunday; labels within and across months', () => {
    expect(weekStart('2026-10-17')).toBe('2026-10-11');
    expect(weekStart('2026-10-11')).toBe('2026-10-11');
    expect(weekLabel('2026-10-11')).toBe('Oct 11 \u2013 17');
    expect(weekLabel('2026-12-27')).toBe('Dec 27 \u2013 Jan 2');
    expect(weekLabel('2026-09-27')).toBe('Sep 27 \u2013 Oct 3');
    expect(columnLabel('2026-10-11')).toBe('Sun 11');
    expect(longDate('2026-10-20')).toBe('Oct 20, 2026');
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
    // local days right around the switches
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
  });

  it('detail labels', () => {
    expect(seasonsText(5)).toBe('5 seasons');
    expect(seasonsText(1)).toBe('1 season');
    expect(seasonsText(null)).toBe('');
    expect(haveText(62, 62)).toBe('62 of 62 episodes');
    expect(metaText({ year: 2008, network: 'AMC', status: 'Ended' })).toBe('2008 \u00b7 AMC \u00b7 Ended');
    expect(openingSeason(SEV_DETAIL.seasons, 1)).toBe(1);
    expect(openingSeason(SEV_DETAIL.seasons, null)).toBe(2);        // the latest, never Specials
    expect(openingSeason(SEV_DETAIL.seasons, 7)).toBe(2);
  });
});

// ---------------------------------------------------------------- calendar

describe('Calendar', () => {
  it('opens by default: the current week grid with its states, then Upcoming', async () => {
    page(true);
    const cards = await screen.findAllByTestId('tv-cal-card');
    expect(screen.getByTestId('tv-tab-calendar').getAttribute('aria-selected')).toBe('true');
    expect(screen.getByTestId('tv-calendar').getAttribute('data-layout')).toBe('grid');
    expect(calCalls()).toEqual(['start=2026-10-11&days=28']);
    expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 11 \u2013 17');
    expect(screen.queryByTestId('tv-cal-today')).toBeNull();              // on the current week
    expect(cards.map(c => c.getAttribute('aria-label'))).toEqual([
      'Downloaded: Severance S02E01', 'Missing: Severance S02E02', 'Upcoming: Severance S02E03']);
    expect(within(cards[0]!).getByTestId('tv-check')).toBeTruthy();
    const days = screen.getAllByTestId('tv-cal-day');
    expect(days.map(d => d.firstElementChild!.textContent)).toEqual(['Sun 11', 'Mon 12', 'Tue 13', 'Wed 14', 'Thu 15', 'Fri 16', 'Sat 17']);
    expect(days[6]!.firstElementChild!.getAttribute('aria-current')).toBe('date');
    const up = screen.getByTestId('tv-cal-upcoming');
    expect(within(up).getAllByTestId('tv-agenda-day').map(d => d.firstElementChild!.textContent)).toEqual(['Mon, Oct 19', 'Thu, Oct 29']);
    expect(within(up).getAllByTestId('tv-state-word').map(w => w.textContent)).toEqual(['Upcoming', 'Upcoming']);
  });

  it('week navigation, Today, and an empty week; the week sits in the URL', async () => {
    page(true);
    await screen.findAllByTestId('tv-cal-card');
    fireEvent.click(screen.getByTestId('tv-cal-next'));
    await screen.findAllByTestId('tv-cal-card');
    expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 18 \u2013 24');
    expect(loc).toBe('/settings/tv?week=2026-10-18');
    expect(screen.queryByTestId('tv-cal-upcoming')).toBeNull();           // Upcoming: current week only
    fireEvent.click(screen.getByTestId('tv-cal-next'));
    fireEvent.click(screen.getByTestId('tv-cal-next'));
    expect((await screen.findByTestId('tv-cal-empty')).textContent).toBe('No episodes this week');
    expect(screen.getByTestId('tv-cal-label').textContent).toBe('Nov 1 \u2013 7');
    fireEvent.click(screen.getByTestId('tv-cal-today'));
    await waitFor(() => expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 11 \u2013 17'));
    expect(loc).toBe('/settings/tv');
    expect(calCalls()).toEqual(['start=2026-10-11&days=28', 'start=2026-10-18&days=7', 'start=2026-10-25&days=7',
      'start=2026-11-01&days=7', 'start=2026-10-11&days=28']);
  });

  it('loading shows skeletons, no spinner', async () => {
    let release!: () => void;
    const fetch0 = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn(async (u: string, i?: RequestInit) => {
      if (String(u).startsWith('/api/settings/tv/calendar')) await new Promise<void>(r => { release = r; });
      return fetch0(u, i);
    }));
    page(true);
    const sk = await screen.findByTestId('tv-cal-loading');
    expect(sk.querySelectorAll('.tvc-pulse').length).toBe(7 * 4);          // 3 cards and the header per column
    expect(document.querySelector('.tvi-spin')).toBeNull();
    await act(async () => release());
    await screen.findAllByTestId('tv-cal-card');
  });

  it('phone (and any width under 1000px) gets the agenda by day', async () => {
    page(false);
    const days = await screen.findAllByTestId('tv-agenda-day');
    expect(screen.getByTestId('tv-calendar').getAttribute('data-layout')).toBe('agenda');
    expect(days.slice(0, 3).map(d => d.firstElementChild!.textContent)).toEqual(['Mon, Oct 12', 'Fri, Oct 16', 'Today']);
    expect((days[0]!.firstElementChild as HTMLElement).style.position).toBe('sticky');
    expect(screen.getAllByTestId('tv-agenda-row').slice(0, 3).map(r => within(r).getByTestId('tv-state-word').textContent))
      .toEqual(['Downloaded', 'Missing', 'Upcoming']);
    cleanup();
    vi.stubGlobal('ResizeObserver', class {
      cb: ResizeObserverCallback;
      constructor(cb: ResizeObserverCallback) { this.cb = cb; }
      observe() { this.cb([{ contentRect: { width: 912 } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
      disconnect() {}
      unobserve() {}
    });
    page(true);                                                             // a 1024 desktop: 912px of content
    await screen.findAllByTestId('tv-agenda-day');
    expect(screen.getByTestId('tv-calendar').getAttribute('data-layout')).toBe('agenda');
  });

  it('a card opens Show detail at its season and episode, highlighted for 1.6s; Back returns', async () => {
    page(true);
    fireEvent.click((await screen.findAllByTestId('tv-cal-card'))[1]!);
    await screen.findByTestId('tv-detail-title');
    expect(loc).toBe('/settings/tv?show=1&season=2&episode=2');
    // season 2 in the fixture has E03-E05; deep link a listed one instead
    cleanup();
    page(true, '/settings/tv?show=1&season=2&episode=4');
    await screen.findByTestId('tv-detail-title');
    const rows = screen.getAllByTestId('tv-episode');
    expect(rows.map(r => r.getAttribute('data-highlight'))).toEqual([null, 'true', null]);
    expect(rows.map(r => r.getAttribute('data-state'))).toEqual(['downloaded', 'missing', 'upcoming']);
    expect(document.head.innerHTML + document.body.innerHTML).toContain(`animation:tvd-hl ${HIGHLIGHT_MS}ms`);
    expect(HIGHLIGHT_MS).toBe(1600);
    await waitFor(() => expect(screen.getAllByTestId('tv-episode')[1]!.getAttribute('data-highlight')).toBeNull(), { timeout: 2500 });
  });
});

// ---------------------------------------------------------------- detail

describe('Show detail', () => {
  it('header, seasons with Specials last, episode rows; Back goes to the view it came from', async () => {
    page(true);
    fireEvent.click((await screen.findAllByTestId('tv-cal-card'))[0]!);
    expect((await screen.findByTestId('tv-detail-title')).textContent).toBe('Severance');
    expect(screen.getByText('2022 \u00b7 Apple TV+ \u00b7 Returning')).toBeTruthy();
    expect(screen.getByTestId('tv-detail-have').textContent).toBe('3 of 5 episodes');
    expect(screen.getAllByTestId('tv-season-chip').map(c => [c.textContent, c.getAttribute('aria-selected')]))
      .toEqual([['Season 1', 'false'], ['Season 2', 'true'], ['Specials', 'false']]);
    fireEvent.click(screen.getAllByTestId('tv-season-chip')[2]!);
    await waitFor(() => expect(screen.getAllByTestId('tv-episode').map(r => r.textContent)).toEqual(['E01Main TitleUpcoming']));
    fireEvent.click(screen.getByTestId('tv-detail-back'));
    await screen.findAllByTestId('tv-cal-card');
    expect(loc).toBe('/settings/tv');
  });

  it("a failed load says Couldn't load show with Retry", async () => {
    page(true, '/settings/tv?show=77');
    expect((await screen.findByRole('alert')).textContent).toContain("Couldn't load show");
    fireEvent.click(screen.getByTestId('tv-detail-retry'));
    await waitFor(() => expect(calls.filter(c => c.url === '/api/settings/tv/shows/77')).toHaveLength(2));
    expect(screen.queryByTestId('tv-detail-back')).toBeTruthy();
  });
});

// ---------------------------------------------------------------- search

describe('Search', () => {
  const type = (v: string) => fireEvent.change(screen.getByTestId('tv-search'), { target: { value: v } });

  it('waits 250ms and 2 characters; library first with In library, then TMDB with Add; the tabs dim', async () => {
    page(true);
    await screen.findAllByTestId('tv-cal-card');
    type('S');
    expect(screen.getByTestId('tv-section').getAttribute('data-view')).toBe('calendar');
    type('Se'); type('Sev');
    expect(SEARCH_DEBOUNCE_MS).toBe(250);
    const rows = await screen.findAllByTestId('tv-search-row');
    expect(calls.filter(c => c.url.startsWith('/api/settings/tv/find')).map(c => c.url)).toEqual(['/api/settings/tv/find?q=Sev']);
    expect(rows.map(r => r.textContent)).toEqual(['Severance (2022)2 seasonsIn library', 'Severance Pay (2026)1 seasonAdd']);
    expect(rows[0]!.getAttribute('data-in-library')).toBe('true');
    expect((screen.getByTestId('tv-tabs').parentElement as HTMLElement).style.opacity).toBe('0.4');
    fireEvent.keyDown(screen.getByTestId('tv-search'), { key: 'Escape' });
    await screen.findAllByTestId('tv-cal-card');                            // back to the view it replaced
  });

  it('a library row opens Show detail; no results says No results; the x clears', async () => {
    page(false);
    await screen.findByTestId('tv-search');
    type('Severance');
    fireEvent.click(within((await screen.findAllByTestId('tv-search-row'))[0]!).getByRole('button'));
    await screen.findByTestId('tv-detail-title');
    expect(loc).toBe('/settings/tv?show=1');
    type('nothing');
    expect((await screen.findByTestId('tv-search-empty')).textContent).toBe('No results');
    fireEvent.click(screen.getByTestId('tv-search-clear'));
    expect((screen.getByTestId('tv-search') as HTMLInputElement).value).toBe('');
    await screen.findAllByTestId('tv-agenda-day');
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

  it('Add with 2+ libraries asks where: nothing preselected, Add at 40% until one is picked', async () => {
    paths = [TV, ANIME];
    page(true);
    await screen.findByTestId('tv-search');
    type('Severance');
    fireEvent.click(await screen.findByTestId('tv-add'));
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

  it('phone: the picker is a bottom sheet; Cancel closes it without adding', async () => {
    paths = [TV, ANIME];
    page(false);
    await screen.findByTestId('tv-search');
    type('Severance');
    fireEvent.click(await screen.findByTestId('tv-add'));
    expect(screen.getByTestId('tv-add-sheet-backdrop')).toBeTruthy();
    expect(screen.getByTestId('tv-add').style.height).toBe('40px');
    fireEvent.click(screen.getByTestId('tv-add-cancel'));
    expect(screen.queryByTestId('tv-add-picker')).toBeNull();
    expect(calls.some(c => c.method === 'POST')).toBe(false);
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
  it('Calendar | Import | Libraries | Options in the URL, so Back works; Options holds Upgrades', async () => {
    page(true);
    await screen.findAllByTestId('tv-cal-card');
    expect(screen.getByTestId('tv-title').textContent).toBe('TV');
    expect(screen.getAllByRole('tab').slice(0, 4).map(t => t.textContent)).toEqual(['Calendar', 'Import', 'Libraries', 'Options']);
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
    await screen.findAllByTestId('tv-cal-card');
    fireEvent.click(screen.getByTestId('tv-tab-options'));
    fireEvent.click(screen.getByTestId('tv-tab-import'));
    expect(loc).toBe('/settings/tv?view=import');
    fireEvent.click(screen.getByTestId('back'));
    await waitFor(() => expect(screen.getByTestId('tv-tab-options').getAttribute('aria-selected')).toBe('true'));
    fireEvent.click(screen.getByTestId('back'));
    await waitFor(() => expect(screen.getByTestId('tv-tab-calendar').getAttribute('aria-selected')).toBe('true'));
    expect(loc).toBe('/settings/tv');
  });

  it('Import has an amber badge with the review count', async () => {
    unmatched = [{ id: 7, path_id: TV.id, path_name: 'TV', folder: 'Mystery', guess_title: null, guess_year: null, candidates: [], reason: null, tier: 'none' }];
    page(true);
    const b = await screen.findByTestId('tv-import-badge');
    expect([b.textContent, b.style.background]).toEqual(['1', 'rgb(245, 158, 11)']);
  });

  it('No TV libraries: Calendar and Search offer Choose libraries, which opens Libraries', async () => {
    paths = [];
    page(false);
    expect((await screen.findByTestId('tv-no-libraries')).textContent).toBe('No TV libraries' + 'Choose libraries');
    expect(calCalls()).toEqual([]);
    fireEvent.change(screen.getByTestId('tv-search'), { target: { value: 'Severance' } });
    expect(screen.getByTestId('tv-no-libraries')).toBeTruthy();
    fireEvent.click(screen.getByTestId('tv-choose-libraries'));
    expect(loc).toBe('/settings/tv?view=libraries');
    expect((screen.getByTestId('tv-search') as HTMLInputElement).value).toBe('');
  });

  it('phone: the control is a full-width row under search that scrolls sideways', async () => {
    page(false);
    await screen.findAllByTestId('tv-agenda-day');
    expect(screen.queryByTestId('tv-title')).toBeNull();                   // the sheet header says TV
    const scroller = screen.getByTestId('tv-tabs').parentElement as HTMLElement;
    expect(scroller.style.overflowX).toBe('auto');
    expect(screen.getByTestId('tv-tabs').style.width).toBe('100%');
    const order = Array.from(screen.getByTestId('tv-section').children).map(c => c.querySelector('[data-testid]')?.getAttribute('data-testid') || c.getAttribute('data-testid'));
    expect(order.indexOf('tv-search')).toBeLessThan(order.indexOf('tv-tabs'));
  });

  it('no helper text, American English, sentence case', () => {
    const src = ['TvLibrarySettings.tsx', 'TvCalendar.tsx', 'TvSearch.tsx', 'TvShowDetail.tsx', 'tvCalendarApi.ts']
      .map(f => readFileSync(resolve(__dirname, '../src/components/library/' + f), 'utf8')).join('\n');
    expect(src).not.toMatch(/hint=|note=|helper=/i);
    expect(src).not.toMatch(/colour|favourite|catalogue|cancelled/i);
    for (const label of ['Search shows', 'No episodes this week', 'Upcoming', 'Downloaded', 'Missing', 'In library', 'Add to',
      "Couldn't add show", 'No results', "Couldn't load show", 'Wrong show?', 'Specials', 'No TV libraries', 'Choose libraries', 'Today', 'Tomorrow'])
      expect(src).toContain(label);
  });
});
