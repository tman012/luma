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
import { CAL_MAX_W, CARD_TEXT_H, POLL_MS, WeekGrid, WeekHero, airLine, keepIfSame, tvCalendarCss, weekCounts } from '@/components/library/TvCalendar';
import { ModalHostContext, type ModalHostContextValue } from '@/contexts/ModalHostContext';
import {
  addDays, agendaLabel, byDay, episodeState, isDay, localDay, seasonsText, shortDate, weekDays, weekLabel, weekStart, type CalEpisode,
  timeRange, sortDay,
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
let libShows: unknown[];
const calls: { url: string; method: string; body: unknown }[] = [];
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'], shouldAdvanceTime: true });
  vi.setSystemTime(NOW);
  calls.length = 0;
  paths = [TV];
  unmatched = [];
  addStatus = 201;
  libShows = [];
  episodes = [ep(1, '2026-10-12', true), ep(2, '2026-10-16', false), ep(3, '2026-10-17', false),
    ep(4, '2026-10-19', false), ep(5, '2026-10-29', false, 'Dark', 1, 1)];
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u);
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/settings/tv') return res({ paths, upgrades: { mode: 'keep_both' }, rescan_interval_h: 6 });
    if (url === '/api/settings/tv/libraries') return res({ selected: paths.map(p => p.id), libraries: [TV, ANIME].map(l => ({ ...l, selected: paths.some(p => p.id === l.id) })) });
    if (url === '/api/settings/tv/library') return res({ shows: libShows, unmatched, scan: { running: false, error: null, last_scan: null } });
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

describe('Designer D week cards (v321)', () => {
  const ep = (o: Partial<CalEpisode>): CalEpisode => ({ episode_id: 1, series_id: 1, show: 'Severance', year: 2022, poster_path: '/p.jpg',
    season: 2, episode: 1, title: 'Hello', air_date: '2026-10-12', downloaded: false, ...o });
  it('v326: no thumbnail, 3px status stripe, show / SxxExx · title / air time, bookmark', () => {
    render(<WeekGrid eps={[ep({ airstamp: '2026-10-12T21:00:00-04:00', runtime: 60 } as Partial<CalEpisode>)]} week={'2026-10-11' as Day} today={'2026-10-12' as Day} />);
    const card = screen.getByTestId('tv-agenda-row');
    expect(within(card).queryByTestId('tv-card-poster')).toBeNull();
    expect(card.querySelector('img')).toBeNull();
    expect(within(card).getByTestId('tv-stripe')).toBeTruthy();
    expect(within(card).getByTestId('tv-card-show').textContent).toBe('Severance');
    expect(within(card).getByTestId('tv-card-title').textContent).toBe('S02E01 \u00b7 Hello');
    expect(within(card).getByTestId('tv-air').textContent).not.toBe('');
    expect(within(card).getByTestId('tv-card-bookmark')).toBeTruthy();
    expect(card.style.borderRadius).toBe('10px');
  });
  it('no poster: no initial tile either (v326); v325: no green check, no Downloaded/quality chip; the air time is centered on top', () => {
    render(<WeekGrid eps={[ep({ poster_path: null, downloaded: true, airstamp: '2026-10-12T21:00:00-04:00' } as Partial<CalEpisode>)]} week={'2026-10-11' as Day} today={'2026-10-12' as Day} />);
    const card = screen.getByTestId('tv-agenda-row');
    expect(within(card).queryByTestId('tv-poster-initial')).toBeNull();
    expect(within(card).queryByTestId('tv-check')).toBeNull();
    expect(within(card).queryByTestId('tv-quality-chip')).toBeNull();
    expect(card.textContent).not.toMatch(/Downloaded|1080p|WEB/);
    const air = within(card).getByTestId('tv-air');
    expect(air.style.textAlign).toBe('center');
    expect(card.querySelector('[data-testid="tv-air"] ~ [data-testid="tv-card-body"]')).toBeTruthy();   // above the body
    expect(within(card).getByTestId('tv-stripe')).toBeTruthy();
    expect(card.getAttribute('data-state')).toBe('downloaded');
  });
});

describe('cards collapse to their text (v179)', () => {
  const ep = (o: Partial<CalEpisode>): CalEpisode => ({ episode_id: 1, series_id: 1, show: 'Severance', year: 2022, poster_path: '/p.jpg',
    season: 2, episode: 1, title: 'Hello', air_date: '2026-10-12', downloaded: false, ...o });
  const SIZING = /aspect-ratio|min-height|(^|[;\s])height\s*:|flex-grow|flex:\s*1|align-self:\s*stretch/i;
  const week = [
    ep({ episode_id: 1, airstamp: '2026-10-13T01:00:00Z', runtime: 60 } as Partial<CalEpisode>),     // Mon 9:00 PM EDT
    ep({ episode_id: 2, episode: 2, show: 'The Lord of the Rings: The Rings of Power', title: 'A Very Long Episode Title That Wraps',
      airstamp: '2026-10-13T02:00:00Z', downloaded: true } as Partial<CalEpisode>),
    ep({ episode_id: 3, episode: 3, air_date: '2026-10-14' }),                                      // no air time
    ep({ episode_id: 4, episode: 4, air_date: '2026-10-17', airstamp: '2026-10-17T20:00:00Z' } as Partial<CalEpisode>),
  ];
  it('D cards: text only (v326), content-height columns, no stretch', () => {
    render(<WeekGrid eps={week} week={'2026-10-11' as Day} today={'2026-10-15' as Day} />);
    const cards = screen.getAllByTestId('tv-agenda-row');
    expect(cards).toHaveLength(4);
    for (const card of cards) {
      expect(within(card).getByTestId('tv-stripe')).toBeTruthy();
      expect(within(card).queryByTestId('tv-card-poster') || within(card).queryByTestId('tv-poster-initial')).toBeNull();
      expect(card.style.flex).toBe('0 0 auto');
      const body = within(card).getByTestId('tv-card-body');
      expect(body.children).toHaveLength(1);
      expect((body.lastElementChild as HTMLElement).className).toBe('tvc-text');
    }
    const grid = screen.getByTestId('tv-cal-grid');
    expect([grid.style.alignItems, grid.style.gridAutoRows, grid.style.gridTemplateRows]).toEqual(['start', '', '']);
    for (const d of screen.getAllByTestId('tv-grid-day')) {
      expect(d.style.justifyContent).toBe('flex-start');
      expect(d.style.minHeight).toBe('');
    }
    expect(tvCalendarCss).not.toMatch(/tvc-still|skel/i);
  });
  it('the top line is the start time in the viewer zone for every state, never the date; no air time, no line', () => {
    render(<WeekGrid eps={week} week={'2026-10-11' as Day} today={'2026-10-15' as Day} />);
    const [c1, c2, c3, c4] = screen.getAllByTestId('tv-agenda-row');
    expect([c1, c2, c3, c4].map(c => c!.getAttribute('data-state'))).toEqual(['missing', 'downloaded', 'missing', 'unaired']);
    expect(within(c1!).getByTestId('tv-air').textContent).toBe('9:00 PM');          // a start time, no range
    expect(within(c2!).getByTestId('tv-air').textContent).toContain('10:00 PM');
    expect(within(c2!).queryByTestId('tv-check')).toBeNull();                       // v325: the stripe alone says got-it
    expect(within(c3!).queryByTestId('tv-air')).toBeNull();                         // no air time: the line is gone
    expect(within(c4!).getByTestId('tv-air').textContent).toBe('4:00 PM');
    for (const c of [c1, c2, c4]) expect(within(c!).getByTestId('tv-air').textContent).not.toMatch(/Oct|\d+\/\d+/);
    expect(airLine({ airstamp: '2026-10-19T01:00:00Z' }, 'America/Los_Angeles')).toBe('6:00 PM');   // the viewer's own zone
    expect(airLine({ airstamp: null })).toBeNull();
  });
  it('the show name keeps 28px on both sides (right: hover bookmark; left: v319 centering balance) (1000px and up)', () => {
    expect(tvCalendarCss).toContain('@container tvcal (min-width: 1000px){.tvc-card [data-testid=tv-card-show]{padding-left:28px;padding-right:28px}}');
  });
});

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

  it('states: downloaded with a file; missing before today; unaired from today', () => {
    const t = '2026-10-17';
    expect(episodeState({ downloaded: true, air_date: '2026-10-20' }, t)).toBe('downloaded');
    expect(episodeState({ downloaded: false, air_date: '2026-10-16' }, t)).toBe('missing');
    expect(episodeState({ downloaded: false, air_date: '2026-10-17' }, t)).toBe('unaired');   // airing today folds into unaired
    expect(episodeState({ downloaded: false, air_date: null }, t)).toBe('unaired');
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


describe('Designer D WeekHero (v321)', () => {
  const mk = (id: number, air_date: string, downloaded: boolean): CalEpisode => ({
    episode_id: id, series_id: 1, show: 'Severance', year: 2022, poster_path: null,
    season: 2, episode: id, title: `Episode ${id}`, air_date, downloaded,
  });
  it('This week strip shows count chips only; Today pill when viewing the current week', () => {
    const eps = [mk(1, '2026-10-12', true), mk(2, '2026-10-13', false), mk(3, '2026-10-20', false)];
    render(<WeekHero eps={eps} week={'2026-10-11' as Day} today={'2026-10-17' as Day} />);
    const strip = screen.getByTestId('tv-cal-this-week');
    expect(strip.textContent).toContain('This week');
    expect(strip.textContent).not.toMatch(/Downloaded|Missing|Unaired|Upcoming/i);
    expect([...strip.querySelectorAll('[data-testid=tv-cal-count]')].map(c => c.textContent)).toEqual(['1', '1', '1']);
    expect(screen.getByTestId('tv-cal-today-pill').textContent).toBe('Sat 17 · Today');
    expect(weekCounts(eps, '2026-10-17' as Day)).toEqual({ downloaded: 1, missing: 1, unaired: 1 });
  });
  it('hides the Today pill when the visible week is not the current week', () => {
    render(<WeekHero eps={[]} week={'2026-10-18' as Day} today={'2026-10-17' as Day} />);
    expect(screen.queryByTestId('tv-cal-today-pill')).toBeNull();
    expect(screen.getByTestId('tv-cal-this-week')).toBeTruthy();
  });
});

describe('Calendar', () => {
  it('opens by default: this week as a 7-column grid, Sunday first, with states and air dates; no Today on this week', async () => {
    page(true);
    const rows = await screen.findAllByTestId('tv-agenda-row');
    expect(screen.getByTestId('tv-tab-calendar').getAttribute('aria-selected')).toBe('true');
    expect(calCalls()).toEqual(['start=2026-10-11&days=7']);
    expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 11 \u2013 17');
    expect(screen.queryByTestId('tv-cal-today')).toBeNull();
    expect(screen.getByTestId('tv-cal-hero')).toBeTruthy();
    expect(screen.getByTestId('tv-cal-today-pill')).toBeTruthy();
    expect(screen.getAllByTestId('tv-grid-day').map(d => d.firstElementChild!.textContent)).toEqual(
      ['Sun 11', 'Mon 12', 'Tue 13', 'Wed 14', 'Thu 15', 'Fri 16', 'Sat 17']);
    expect(rows.map(r => r.getAttribute('aria-label'))).toEqual([
      'Downloaded: Severance S02E01', 'Missing: Severance S02E02', 'Unaired: Severance S02E03']);
    // v321 Designer D: 3px status stripe on every card
    expect(rows.every(r => within(r).queryByTestId('tv-stripe'))).toBe(true);
    expect(rows.every(r => within(r).queryByTestId('tv-check'))).toBe(false);  // fixtures have no airstamp
    // v325 (Taylor): no state words and no quality chip; the stripe is the only got-it indicator
    expect(rows.some(r => /Missing|Unaired/.test(r.textContent || ''))).toBe(false);
    expect(rows.map(r => within(r).queryByTestId('tv-quality-chip')?.textContent ?? null)).toEqual([null, null, null]);
    expect(rows[0]!.textContent).not.toContain('Downloaded');
    expect(rows[0]!.textContent).toContain('Episode 1');
    const name = within(rows[0]!).getByTestId('tv-card-show');                     // 13/600, one line
    expect([name.style.fontSize, name.style.fontWeight, name.style.whiteSpace]).toEqual(['13px', '600', 'nowrap']);
    expect(name.getAttribute('title')).toBe(name.textContent);
    const title = within(rows[0]!).getByTestId('tv-card-title');                   // SxxEyy · title, 12px, 70%
    expect(name.nextElementSibling).toBe(title);
    expect(title.textContent).toMatch(/^S\d\dE\d\d \u00b7 /);
    expect([title.style.fontSize, title.style.color, title.style.whiteSpace])
      .toEqual(['12px', 'rgba(255, 255, 255, 0.7)', 'nowrap']);
    expect(within(rows[0]!).queryByTestId('tv-quality-chip')).toBeNull();          // v325: no chip on week cards
    // the top row: ‹ range › together in the true center (a 1fr auto 1fr grid), no separate toolbar
    const center = screen.getByTestId('tv-cal-center');
    expect(center.contains(screen.getByTestId('tv-cal-prev')) && center.contains(screen.getByTestId('tv-cal-next'))).toBe(true);
    expect(screen.getByTestId('tv-top-row').className).toBe('tvt-top');                 // v158c: minmax(200px, 320px) 1fr auto
    expect(screen.queryByTestId('tv-cal-toolbar')).toBeNull();
    // the only control on a card is the hover bookmark (v158a); no links
    expect(rows.every(r => r.tagName === 'DIV' && !r.querySelector('a')
      && [...r.querySelectorAll('button')].every(b => b.getAttribute('data-testid') === 'tv-card-bookmark'))).toBe(true);
  });

  it('asks again quietly on window focus and every 60s while visible (no placeholder)', async () => {
    const ivals: [() => void, number][] = [];
    const real = window.setInterval;
    vi.spyOn(window, 'setInterval').mockImplementation(((fn: () => void, ms: number) => { ivals.push([fn, ms]); return real(() => {}, 1e9); }) as typeof window.setInterval);
    page(true);
    await screen.findAllByTestId('tv-agenda-row');
    expect(calCalls()).toEqual(['start=2026-10-11&days=7']);
    window.dispatchEvent(new Event('focus'));
    await waitFor(() => expect(calCalls()).toHaveLength(2));
    expect(screen.queryByTestId('tv-cal-loading')).toBeNull();                       // quiet: the grid stays
    const poll = ivals.find(([, ms]) => ms === POLL_MS)!;
    expect(POLL_MS).toBe(60_000);
    poll[0]();
    await waitFor(() => expect(calCalls()).toHaveLength(3));
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    poll[0]();
    window.dispatchEvent(new Event('focus'));
    await new Promise(r => setTimeout(r, 20));
    expect(calCalls()).toHaveLength(3);                                               // hidden: no polling
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

  it('v325 (Taylor): no skeleton while loading, an empty invisible reserve; the week then rises in', async () => {
    let release!: () => void;
    const fetch0 = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn(async (u: string, i?: RequestInit) => {
      if (String(u).startsWith('/api/settings/tv/calendar')) await new Promise<void>(r => { release = r; });
      return fetch0(u, i);
    }));
    page(true);
    const ph = await screen.findByTestId('tv-cal-loading');
    expect(ph.children).toHaveLength(0);                                             // nothing visible: no strips, no cards
    expect(ph.getAttribute('aria-busy')).toBe('true');
    expect(ph.style.background).toBe('');
    expect(parseInt(ph.style.minHeight, 10)).toBe(29 + 3 * (CARD_TEXT_H + 8));        // the height is reserved
    expect(screen.queryAllByTestId('tv-cal-loading-day')).toHaveLength(0);
    expect(screen.queryAllByTestId('tv-cal-loading-card')).toHaveLength(0);
    expect(document.querySelector('.tvi-spin, .tvc-pulse, [class*="spin"]')).toBeNull();
    expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 11 \u2013 17');   // the toolbar stays put
    await act(async () => release());
    await screen.findAllByTestId('tv-agenda-row');
    expect(screen.getByTestId('tv-cal-week').className).toBe('tvc-rise');           // first load: fade + rise (MOTION tokens)
  });
});

// ---------------------------------------------------------------- search

describe('Search', () => {
  const type = (v: string) => fireEvent.change(screen.getByTestId('tv-search'), { target: { value: v } });
  // v182 (Taylor): adding is the Series page's own Add series dialog; the toolbar finds only your shows
  const addType = (v: string) => fireEvent.change(screen.getByTestId('tv-add-series-search'), { target: { value: v } });
  const openAdd = async () => {
    page(true, '/settings/tv?view=series');
    fireEvent.click(await screen.findByTestId('tv-add-series'));
    return screen.getByTestId('tv-add-series-modal');
  };

  it('a compact 36px field; waits 250ms and 2 characters; library first with In library (not clickable), then TMDB with Add', async () => {
    page(true);
    await screen.findAllByTestId('tv-agenda-row');
    // v326 (Taylor): the desktop calendar has no search; the This week strip sits in the top row's left cell
    expect(screen.queryByTestId('tv-search')).toBeNull();
    expect(screen.queryByTestId('tv-search-box')).toBeNull();
    expect(screen.getByTestId('tv-top-left').contains(screen.getByTestId('tv-cal-this-week'))).toBe(true);
    expect(screen.getByTestId('tv-top-row').getAttribute('data-cal')).toBe('true');
    expect(screen.getByTestId('tv-view').contains(screen.getByTestId('tv-cal-grid'))).toBe(true);
    expect(screen.getByTestId('tv-view').querySelector('[data-testid="tv-cal-hero"]')).toBeNull();
    {
    const row = screen.getByTestId('tv-top-row');
    expect(row.className).toBe('tvt-top');
    const center = screen.getByTestId('tv-top-center');
    expect([...within(center).getByTestId('tv-cal-center').children].map(c => c.getAttribute('data-testid')))
      .toEqual(['tv-cal-prev', 'tv-cal-label', 'tv-cal-next']);
    expect(screen.getByTestId('tv-top-right').contains(screen.getByTestId('tv-tabs'))).toBe(true);
    }
    cleanup();
    page(true, '/settings/tv?view=series');
    await screen.findByTestId('tv-search');
    let field = screen.getByTestId('tv-search') as HTMLInputElement;
    expect([field.placeholder, field.style.height]).toEqual(['Search your shows', '36px']);     // v181: the calendar finds only your shows
    expect([screen.getByTestId('tv-search-box').style.width, screen.getByTestId('tv-search-box').style.maxWidth]).toEqual(['100%', '320px']);
    // the top row (v158c): a 3-column grid (minmax(200px, 320px) 1fr auto); search left, ‹ range › centered, tabs right
    const row = screen.getByTestId('tv-top-row');
    expect(row.className).toBe('tvt-top');
    expect(screen.getByTestId('tv-top-left').className).toBe('tvt-top-search');
    expect(screen.getByTestId('tv-top-left').contains(screen.getByTestId('tv-search-box'))).toBe(true);
    expect(screen.getByTestId('tv-top-center').className).toBe('tvt-top-week');
    expect(screen.getByTestId('tv-top-right').className).toBe('tvt-top-tabs');
    expect(screen.getByTestId('tv-top-right').contains(screen.getByTestId('tv-tabs'))).toBe(true);
    expect(screen.queryByTestId('tv-title')).toBeNull();                                           // no 'TV' heading
    cleanup();
    page(true, '/settings/tv?view=series');                                                        // v182: the Series toolbar too
    await screen.findByTestId('tv-series-empty');
    field = screen.getByTestId('tv-search') as HTMLInputElement;
    expect(field.placeholder).toBe('Search your shows');
    type('Sev');
    await new Promise(r => setTimeout(r, SEARCH_DEBOUNCE_MS + 50));
    expect(calls.filter(c => c.url.startsWith('/api/settings/tv/find'))).toEqual([]);
    expect(screen.queryAllByTestId('tv-add')).toHaveLength(0);
    expect(screen.queryAllByTestId('tv-search-row')).toHaveLength(0);
    // Add series: its own dialog searches TMDB (250ms, 2 characters); library first with In library, then Add
    fireEvent.click(screen.getByTestId('tv-add-series'));
    const modal = screen.getByTestId('tv-add-series-modal');
    expect(modal.getAttribute('aria-label')).toBe('Add series');
    addType('S');
    addType('Se'); addType('Sev');
    expect(SEARCH_DEBOUNCE_MS).toBe(250);
    const rows = await within(modal).findAllByTestId('tv-search-row');
    expect(calls.filter(c => c.url.startsWith('/api/settings/tv/find')).map(c => c.url)).toEqual(['/api/settings/tv/find?q=Sev']);
    expect(rows.map(r => r.textContent)).toEqual(['Severance (2022)2 seasonsIn library', 'Severance Pay (2026)1 seasonAdd']);
    expect(rows[0]!.getAttribute('data-in-library')).toBe('true');
    expect(rows[0]!.querySelector('button, a')).toBeNull();
    expect(rows[1]!.firstElementChild!.getAttribute('style')).toContain('height: 72px');            // the same 72px rows
    fireEvent.click(within(modal).getByTestId('tv-add-series-done'));
    expect(screen.queryByTestId('tv-add-series-modal')).toBeNull();
    fireEvent.focus(field);
    expect(screen.getByTestId('tv-series-empty')).toBeTruthy();                                    // the view stays under it
    fireEvent.keyDown(screen.getByTestId('tv-search'), { key: 'Escape' });
    expect(screen.queryByTestId('tv-search-drop')).toBeNull();               // Esc closes
    expect(field.value).toBe('Sev');
    fireEvent.keyDown(screen.getByTestId('tv-search'), { key: 'Escape' });
    expect(field.value).toBe('');                                            // a second Esc clears
  });

  it('keyboard: ↓/↑ highlight rows (wrapping), Enter adds the highlighted show', async () => {
    await openAdd();
    addType('Severance');
    const rows = await screen.findAllByTestId('tv-search-row');
    const field = screen.getByTestId('tv-add-series-search');
    expect(field.getAttribute('role')).toBe('combobox');
    expect(field.getAttribute('aria-expanded')).toBe('true');
    fireEvent.keyDown(field, { key: 'ArrowDown' });
    expect(rows[0]!.getAttribute('aria-selected')).toBe('true');
    expect(field.getAttribute('aria-activedescendant')).toBe(rows[0]!.id);
    fireEvent.keyDown(field, { key: 'Enter' });                              // In library: nothing to add
    expect(calls.filter(c => c.method === 'POST')).toEqual([]);
    fireEvent.keyDown(field, { key: 'ArrowDown' });
    fireEvent.keyDown(field, { key: 'ArrowDown' });                          // wraps to the first
    expect(screen.getAllByTestId('tv-search-row')[0]!.getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(field, { key: 'ArrowUp' });                            // and back round to the last
    expect(screen.getAllByTestId('tv-search-row')[1]!.getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(field, { key: 'Enter' });                              // v158a: opens the Add picker
    fireEvent.click(screen.getByTestId('tv-add-confirm'));
    await waitFor(() => expect(calls.filter(c => c.method === 'POST').map(c => c.body)).toEqual([{ tmdb_id: 6001, monitor: 'future' }]));
  });

  it('no results says No results; the toolbar x clears', async () => {
    const modal = await openAdd();
    addType('nothing');
    expect((await within(modal).findByTestId('tv-search-empty')).textContent).toBe('No results');
    fireEvent.click(within(modal).getByTestId('tv-add-series-modal-close'));
    type('nothing');
    expect((await screen.findByTestId('tv-search-empty')).textContent).toBe('No shows');      // your shows only
    fireEvent.click(screen.getByTestId('tv-search-clear'));
    expect((screen.getByTestId('tv-search') as HTMLInputElement).value).toBe('');
    await screen.findByTestId('tv-series-empty');
  });

  it('Add with one library opens Monitor (Future episodes) plus the path line, then shows In library', async () => {
    await openAdd();
    addType('Severance');
    fireEvent.click(await screen.findByTestId('tv-add'));
    const picker = screen.getByTestId('tv-add-picker');
    expect(within(picker).queryAllByTestId('tv-add-path')).toHaveLength(0);             // no radio with one library
    expect(within(picker).getByTestId('tv-add-path-line').textContent).toContain('TV');
    const mon = within(picker).getByTestId('tv-add-monitor-select');
    expect(mon.textContent).toBe('Future episodes');
    fireEvent.click(mon);
    expect(within(picker).getAllByRole('option').map(o => o.textContent))
      .toEqual(['All episodes', 'Future episodes', 'Missing episodes', 'First season', 'Last season', 'None']);
    fireEvent.click(within(picker).getByRole('option', { name: 'Missing episodes' }));
    fireEvent.click(within(picker).getByTestId('tv-add-confirm'));
    await waitFor(() => expect(screen.getAllByTestId('tv-in-library')).toHaveLength(2));
    expect(calls.filter(c => c.method === 'POST').map(c => [c.url, c.body])).toEqual([['/api/settings/tv/shows', { tmdb_id: 6001, monitor: 'missing' }]]);
    expect(screen.queryByTestId('tv-add-picker')).toBeNull();
  });

  it('Add with 2+ libraries asks where: nothing preselected, Add at 40% until one is picked; Cancel adds nothing', async () => {
    paths = [TV, ANIME];
    await openAdd();
    addType('Severance');
    fireEvent.click(await screen.findByTestId('tv-add'));
    fireEvent.click(screen.getByTestId('tv-add-cancel'));
    expect(screen.queryByTestId('tv-add-picker')).toBeNull();
    expect(calls.some(c => c.method === 'POST')).toBe(false);
    fireEvent.click(screen.getByTestId('tv-add'));
    const picker = screen.getByTestId('tv-add-picker');
    expect(picker.style.position).toBe('');                                 // in the dropdown's flow, under the row
    expect([picker.style.background, picker.style.backdropFilter, picker.style.border])     // the dropdown's surface
      .toEqual(['rgba(21, 16, 42, 0.92)', 'blur(24px)', '1px solid rgba(255, 255, 255, 0.08)']);
    expect(within(picker).getAllByTestId('tv-add-path').map(r => [r.textContent, r.getAttribute('aria-checked')]))
      .toEqual([['TV/media/TV', 'false'], ['Anime/media/Anime', 'false']]);
    const add = within(picker).getByTestId('tv-add-confirm') as HTMLButtonElement;
    expect([add.disabled, add.style.opacity]).toEqual([true, '0.4']);
    fireEvent.click(within(picker).getAllByTestId('tv-add-path')[1]!);
    expect([add.disabled, add.style.opacity]).toEqual([false, '1']);
    fireEvent.click(add);
    await waitFor(() => expect(screen.getAllByTestId('tv-in-library')).toHaveLength(2));
    expect(calls.find(c => c.method === 'POST')?.body).toEqual({ tmdb_id: 6001, path_id: ANIME.id, monitor: 'future' });
  });

  it("a failed Add says Couldn't add show with Retry", async () => {
    addStatus = 502;
    await openAdd();
    addType('Severance');
    fireEvent.click(await screen.findByTestId('tv-add'));
    fireEvent.click(screen.getByTestId('tv-add-confirm'));
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
  it('Series | Calendar | Import only (v327) in the URL (Calendar the default), so Back works; no Import badge', async () => {
    unmatched = [{ id: 7, path_id: TV.id, path_name: 'TV', folder: 'Mystery', guess_title: null, guess_year: null, candidates: [], reason: null, tier: 'none' }];
    page(true);
    await screen.findAllByTestId('tv-agenda-row');
    expect(screen.queryByTestId('tv-title')).toBeNull();
    expect(screen.getAllByTestId(/^tv-tab-/).filter(t => t.getAttribute('role') === 'tab').map(t => t.textContent)).toEqual(['Series', 'Calendar', 'Import']);
    expect(screen.getByTestId('tv-tab-calendar').getAttribute('aria-selected')).toBe('true');
    expect(screen.queryByTestId('tv-import-badge')).toBeNull();
    fireEvent.click(screen.getByTestId('tv-tab-series'));
    expect(await screen.findByTestId(/^tv-series(-empty)?$/)).toBeTruthy();
    expect(screen.getByTestId('tv-section').getAttribute('data-view')).toBe('series');
    expect(screen.queryByTestId('tv-tab-options')).toBeNull();
    expect(screen.queryByTestId('tv-tab-libraries')).toBeNull();
    fireEvent.click(screen.getByTestId('tv-tab-import'));
    await screen.findByTestId('tv-import-start');
    expect(loc).toBe('/settings/tv?view=import');
    expect(screen.queryByTestId('tv-paths')).toBeNull();
    expect(screen.queryByTestId('tv-upgrades')).toBeNull();
  });

  it('Back walks the tabs (history)', async () => {
    function Back() { const nav = useNavigate(); return <button type="button" data-testid="back" onClick={() => nav(-1)} />; }
    render(<MemoryRouter initialEntries={['/settings/tv']}><TvLibrarySettings wide /><Where /><Back /></MemoryRouter>);
    await screen.findAllByTestId('tv-agenda-row');
    fireEvent.click(screen.getByTestId('tv-tab-series'));
    fireEvent.click(screen.getByTestId('tv-tab-import'));
    expect(loc).toBe('/settings/tv?view=import');
    fireEvent.click(screen.getByTestId('back'));
    await waitFor(() => expect(screen.getByTestId('tv-tab-series').getAttribute('aria-selected')).toBe('true'));
    fireEvent.click(screen.getByTestId('back'));
    await waitFor(() => expect(screen.getByTestId('tv-tab-calendar').getAttribute('aria-selected')).toBe('true'));
    expect(loc).toBe('/settings/tv');
  });

  it('No TV libraries: Calendar and Search offer Choose libraries, which opens Settings > TV', async () => {
    paths = [];
    page(true, '/settings/tv?view=series');
    expect((await screen.findByTestId('tv-no-libraries')).textContent).toBe('No TV libraries' + 'Choose libraries');
    expect(calCalls()).toEqual([]);
    fireEvent.change(screen.getByTestId('tv-search'), { target: { value: 'Severance' } });
    const drop = screen.getByTestId('tv-search-drop');
    expect(within(drop).getByTestId('tv-no-libraries')).toBeTruthy();
    fireEvent.click(within(drop).getByTestId('tv-choose-libraries'));
    expect(loc).toBe('/settings/tv/settings');
  });

  it('no helper text, American English, sentence case', () => {
    const src = ['TvLibrarySettings.tsx', 'TvCalendar.tsx', 'TvSearch.tsx', 'tvCalendarApi.ts']
      .map(f => readFileSync(resolve(__dirname, '../src/components/library/' + f), 'utf8')).join('\n');
    expect(src).not.toMatch(/hint=|note=|helper=/i);
    expect(src).not.toMatch(/colour|favourite|catalogue|cancelled/i);
    for (const label of ['Search shows', 'No episodes this week', 'Unaired', 'Downloaded', 'Missing', 'In library', 'Add to',
      "Couldn't add show", 'No results', 'Wrong show?', 'No TV libraries', 'Choose libraries', 'Today', 'Tomorrow'])
      expect(src).toContain(label);
  });
});


describe('quiet re-asks', () => {
  it('keep the same object (no re-render) unless an episode changed', () => {
    const ep = { episode_id: 1, series_id: 1, show: 'S', year: 2020, poster_path: null, season: 1, episode: 1, title: 't',
      air_date: '2026-10-18', downloaded: false } as never;
    const cal = (eps: unknown[]) => ({ start: '2026-10-18', days: 7, episodes: eps, libraries: 1, refreshed_at: null }) as never;
    const prev = { key: 'k', cal: cal([ep]) };
    expect(keepIfSame(prev, 'k', cal([{ ...(ep as object) }]))).toBe(prev);
    const next = keepIfSame(prev, 'k', cal([{ ...(ep as object), downloaded: true }]));
    expect(next).not.toBe(prev);
    expect(keepIfSame(prev, 'other', cal([ep]))).not.toBe(prev);
  });
});

describe('per-day air-time order', () => {
  const at = (id: number, show: string, airstamp: string | null, season = 1, episode = 1): CalEpisode =>
    ({ ...ep(id, '2026-10-17', false, show, season, episode), airstamp });
  it('by the local air time, a late-night show whose UTC stamp is the next day last among the timed; ties by name, season, episode; untimed last', () => {
    const list = [
      at(1, 'Zed Late Show', '2026-10-18T03:35:00Z'),            // Sat 11:35 PM ET (Sun in UTC)
      at(2, 'No Time Show', null),
      at(3, 'Mid Show', '2026-10-18T00:00:00Z'),                 // Sat 8:00 PM ET
      at(4, 'The Abbott', '2026-10-18T00:00:00Z', 2, 3),         // 8:00 PM tie: name (leading The ignored)
      at(5, 'Early Bird', '2026-10-17T13:00:00Z'),               // Sat 9:00 AM ET (string sort on the UTC clock would put it last)
      at(6, 'Abbott', '2026-10-18T00:00:00Z', 2, 1),             // same name: season, then episode
      at(7, 'Another Untimed', null),
    ];
    expect(sortDay(list).map(e => e.episode_id)).toEqual([5, 6, 4, 3, 1, 7, 2]);
    expect(byDay(list)[0]![1].map(e => e.episode_id)).toEqual([5, 6, 4, 3, 1, 7, 2]);
  });
  it('the week grid renders each day in that order', () => {
    render(<WeekGrid week="2026-10-11" today="2026-10-17" eps={[at(1, 'Late', '2026-10-18T03:30:00Z'), at(2, 'Untimed', null), at(3, 'Early', '2026-10-17T22:00:00Z')]} />);
    const sat = screen.getAllByTestId('tv-grid-day')[6]!;
    expect(within(sat).getAllByTestId('tv-card-show').map(n => n.textContent)).toEqual(['Early', 'Late', 'Untimed']);
  });
});

describe('clickable cards', () => {
  it('a click (thumbnail or text) or Enter opens the show detail at that season and episode', () => {
    const openTv = vi.fn();
    const host = { openTv, openMovie: vi.fn() } as unknown as ModalHostContextValue;
    const e = { ...ep(9, '2026-10-15', true, 'Severance', 2, 7), tmdb_id: 95396 };
    render(<ModalHostContext.Provider value={host}><WeekGrid week="2026-10-11" today="2026-10-17" eps={[e]} /></ModalHostContext.Provider>);
    const card = screen.getByTestId('tv-agenda-row');
    expect([card.getAttribute('role'), card.getAttribute('tabindex')]).toEqual(['button', '0']);
    fireEvent.click(within(card).getByTestId('tv-card-show'));
    fireEvent.keyDown(card, { key: 'Enter' });
    expect(openTv).toHaveBeenCalledTimes(2);
    expect(openTv.mock.calls[0]![0]).toEqual({ tmdbId: 95396, origin: 'tv_calendar', episodeContext: { seasonNumber: 2, episodeNumber: 7 } });
  });
  it('no TMDB id: not clickable', () => {
    const host = { openTv: vi.fn(), openMovie: vi.fn() } as unknown as ModalHostContextValue;
    render(<ModalHostContext.Provider value={host}><WeekGrid week="2026-10-11" today="2026-10-17" eps={[ep(9, '2026-10-15', true)]} /></ModalHostContext.Provider>);
    expect(screen.getByTestId('tv-agenda-row').getAttribute('role')).toBeNull();
  });
});

describe('v325 desktop week navigator (Taylor)', () => {
  it('sits in its own centered row under the title, wider, with Today off to the side', () => {
    const css = readFileSync(resolve(__dirname, '../src/components/library/TvLibrarySettings.tsx'), 'utf8');
    expect(css).toContain('.tvt-top[data-cal]{grid-template-columns:1fr auto 1fr;grid-template-areas:"week week week" "search . tabs"');
    expect(css).toContain('.tvt-top[data-cal] .tvt-top-week [data-testid="tv-cal-today"]{position:absolute');
  });
});
