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
import { CAL_MAX_W, Still, stillChain } from '@/components/library/TvCalendar';
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

describe('stills', () => {
  const ep = (o: Partial<CalEpisode>): CalEpisode => ({ episode_id: 1, series_id: 1, show: 'Severance', year: 2022, poster_path: null,
    season: 2, episode: 1, title: 'Hello', air_date: '2026-10-12', downloaded: false, ...o });
  it('TVDB image, then the TMDB still, then the backdrop; onError moves on; none left is a glass tile, never a broken image', () => {
    const e = ep({ tvdb_image: 'https://artworks.thetvdb.com/banners/e.jpg', still_path: '/s.jpg', backdrop_path: '/b.jpg' });
    const chain = stillChain(e);
    expect(chain).toHaveLength(3);
    expect(chain[0]).toBe('/api/img/tmdb?host=tvdb&path=%2Fbanners%2Fe.jpg');           // through Luma's image proxy, never direct
    expect(stillChain(ep({ tvdb_image: 'https://evil.example/banners/e.jpg' }))).toHaveLength(0);
    expect(chain[1]).toContain('/s.jpg');
    expect(chain[2]).toContain('/b.jpg');
    expect(stillChain(ep({ still_path: '/s.jpg' }))).toHaveLength(1);
    render(<Still e={e} />);
    const box = screen.getByTestId('tv-still');
    expect(box.style.aspectRatio).toBe('16 / 9');
    let img = box.querySelector('img')!;
    expect([img.getAttribute('src'), img.getAttribute('alt'), img.getAttribute('loading')]).toEqual([chain[0], 'Severance S02E01', 'lazy']);
    expect([img.style.width, img.style.objectFit, img.style.opacity, img.style.transition]).toEqual(['100%', 'cover', '0', 'opacity 150ms']);
    fireEvent.error(img);
    img = box.querySelector('img')!;
    expect(img.getAttribute('src')).toBe(chain[1]);
    fireEvent.load(img);
    expect(img.style.opacity).toBe('1');
    fireEvent.error(img);
    expect(box.querySelector('img')!.getAttribute('src')).toBe(chain[2]);
    fireEvent.error(box.querySelector('img')!);
    expect(box.querySelector('img')).toBeNull();
    expect(box.style.background).toBe('rgba(255, 255, 255, 0.04)');
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
      'Downloaded: Severance S02E01', 'Missing: Severance S02E02', 'Unaired: Severance S02E03']);
    // the edge: 4px, full card height, colors from CSS vars (Designer's defaults); the state is in aria-label only
    const edges = rows.map(r => within(r).getByTestId('tv-stripe'));
    expect(edges.map(e => e.style.background)).toEqual([
      'var(--tv-state-downloaded, #22c55e)', 'var(--tv-state-missing, #ef4444)', 'var(--tv-state-unaired, #3b82f6)']);
    expect([edges[0]!.style.width, edges[0]!.style.top, edges[0]!.style.bottom, rows[0]!.style.paddingLeft]).toEqual(['4px', '0px', '0px', '4px']);
    expect(rows.some(r => within(r).queryByTestId('tv-check'))).toBe(false);          // no check: the time line stays put
    expect(rows.some(r => /Downloaded|Missing|Unaired/.test(r.textContent || ''))).toBe(false);   // nothing on screen
    expect(rows[0]!.textContent).toContain('Episode 1');
    const name = within(rows[0]!).getByTestId('tv-card-show');                     // up to 2 lines, 13/600
    expect([name.style.webkitLineClamp, name.style.display, name.style.fontSize, name.style.fontWeight]).toEqual(['2', '-webkit-box', '13px', '600']);
    expect(name.getAttribute('title')).toBe(name.textContent);                     // line 1: the show name only, full width
    const title = within(rows[0]!).getByTestId('tv-card-title');                   // line 2: SxxEyy · title, 12px, 70%, 1 line
    expect(name.nextElementSibling).toBe(title);
    expect(title.textContent).toMatch(/^S\d\dE\d\d \u00b7 /);
    expect([title.style.whiteSpace, title.style.textOverflow, title.style.fontSize, title.style.color])
      .toEqual(['nowrap', 'ellipsis', '12px', 'rgba(255, 255, 255, 0.7)']);
    expect(title.nextElementSibling!.getAttribute('data-testid')).toBe('tv-air');   // line 3: the time
    // the top row: ‹ range › together in the true center (a 1fr auto 1fr grid), no separate toolbar
    const center = screen.getByTestId('tv-cal-center');
    expect(center.contains(screen.getByTestId('tv-cal-prev')) && center.contains(screen.getByTestId('tv-cal-next'))).toBe(true);
    expect(screen.getByTestId('tv-top-row').style.gridTemplateColumns).toBe('1fr auto 1fr');
    expect(screen.queryByTestId('tv-cal-toolbar')).toBeNull();
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
    const cols = within(ph).getAllByTestId('tv-cal-loading-day');                  // mirrors the grid: 7 columns
    expect(cols).toHaveLength(7);
    expect(ph.style.gridTemplateColumns).toBe('repeat(7, minmax(0, 1fr))');
    for (const c of cols) {
      expect((c.firstElementChild as HTMLElement).style.background).toBe('rgba(255, 255, 255, 0.04)');   // a day-header strip
      const cards = within(c).getAllByTestId('tv-cal-loading-card');
      expect(cards.length >= 2 && cards.length <= 3).toBe(true);
      expect(cards[0]!.style.background).toBe('rgba(255, 255, 255, 0.04)');
      expect((cards[0]!.firstElementChild as HTMLElement).style.aspectRatio).toBe('16 / 9');
    }
    expect(document.querySelector('.tvi-spin, .tvc-pulse, [class*="spin"]')).toBeNull();
    expect(screen.getByTestId('tv-cal-label').textContent).toBe('Oct 11 \u2013 17');   // the toolbar stays put
    await act(async () => release());
    await screen.findAllByTestId('tv-agenda-row');
  });
});

// ---------------------------------------------------------------- search

describe('Search', () => {
  const type = (v: string) => fireEvent.change(screen.getByTestId('tv-search'), { target: { value: v } });

  it('a compact 36px field; waits 250ms and 2 characters; library first with In library (not clickable), then TMDB with Add', async () => {
    page(true);
    await screen.findAllByTestId('tv-agenda-row');
    const field = screen.getByTestId('tv-search') as HTMLInputElement;
    expect([field.placeholder, field.style.height]).toEqual(['Search shows', '36px']);
    expect(screen.getByTestId('tv-search-box').style.width).toBe('320px');                        // compact, in the top row
    // the top row: a 3-column grid (1fr auto 1fr); search far left, ‹ range › in the true center, tabs right
    const row = screen.getByTestId('tv-top-row');
    expect([row.style.display, row.style.gridTemplateColumns]).toEqual(['grid', '1fr auto 1fr']);
    expect(screen.getByTestId('tv-top-left').contains(screen.getByTestId('tv-search-box'))).toBe(true);
    expect(screen.getByTestId('tv-top-left').style.justifySelf).toBe('start');
    const center = screen.getByTestId('tv-top-center');
    expect(center.style.justifySelf).toBe('center');
    expect([...within(center).getByTestId('tv-cal-center').children].map(c => c.getAttribute('data-testid')))
      .toEqual(['tv-cal-prev', 'tv-cal-label', 'tv-cal-next']);
    expect(screen.getByTestId('tv-top-right').style.justifySelf).toBe('end');
    expect(screen.getByTestId('tv-top-right').contains(screen.getByTestId('tv-tabs'))).toBe(true);
    expect(screen.queryByTestId('tv-title')).toBeNull();                                           // no 'TV' heading
    type('S');
    expect(screen.getByTestId('tv-section').getAttribute('data-view')).toBe('calendar');
    type('Se'); type('Sev');
    expect(SEARCH_DEBOUNCE_MS).toBe(250);
    const rows = await screen.findAllByTestId('tv-search-row');
    expect(calls.filter(c => c.url.startsWith('/api/settings/tv/find')).map(c => c.url)).toEqual(['/api/settings/tv/find?q=Sev']);
    expect(rows.map(r => r.textContent)).toEqual(['Severance (2022)2 seasonsIn library', 'Severance Pay (2026)1 seasonAdd']);
    expect(rows[0]!.getAttribute('data-in-library')).toBe('true');
    expect(rows[0]!.querySelector('button, a')).toBeNull();
    const drop = screen.getByTestId('tv-search-drop');                       // a 480px glass dropdown on the field's left edge
    expect([drop.style.width, drop.style.left, drop.style.position, drop.style.borderRadius, drop.style.background, drop.style.backdropFilter])
      .toEqual(['480px', '0px', 'absolute', '12px', 'rgba(255, 255, 255, 0.06)', 'blur(16px)']);
    expect(rows[1]!.firstElementChild!.getAttribute('style')).toContain('height: 72px');            // the same 72px rows
    expect(screen.getAllByTestId('tv-agenda-row').length).toBeGreaterThan(0);                     // the view stays under it
    fireEvent.keyDown(screen.getByTestId('tv-search'), { key: 'Escape' });
    expect(screen.queryByTestId('tv-search-drop')).toBeNull();               // Esc closes
    expect(field.value).toBe('Sev');
    fireEvent.keyDown(screen.getByTestId('tv-search'), { key: 'Escape' });
    expect(field.value).toBe('');                                            // a second Esc clears
  });

  it('keyboard: ↓/↑ highlight rows (wrapping), Enter adds the highlighted show; a click outside closes', async () => {
    page(true);
    await screen.findAllByTestId('tv-agenda-row');
    type('Severance');
    const rows = await screen.findAllByTestId('tv-search-row');
    const field = screen.getByTestId('tv-search');
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
    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() => expect(calls.filter(c => c.method === 'POST').map(c => c.body)).toEqual([{ tmdb_id: 6001 }]));
    fireEvent.pointerDown(document.body);
    expect(screen.queryByTestId('tv-search-drop')).toBeNull();
    fireEvent.focus(field);                                                  // focus opens it again
    expect(screen.getByTestId('tv-search-drop')).toBeTruthy();
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
    expect(picker.style.position).toBe('');                                 // in the dropdown's flow, under the row
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
  it('Series | Calendar | Import | Libraries | Options in the URL (Calendar the default), so Back works; Options holds Upgrades; no Import badge', async () => {
    unmatched = [{ id: 7, path_id: TV.id, path_name: 'TV', folder: 'Mystery', guess_title: null, guess_year: null, candidates: [], reason: null, tier: 'none' }];
    page(true);
    await screen.findAllByTestId('tv-agenda-row');
    expect(screen.queryByTestId('tv-title')).toBeNull();
    expect(screen.getAllByRole('tab').slice(0, 5).map(t => t.textContent)).toEqual(['Series', 'Calendar', 'Import', 'Libraries', 'Options']);
    expect(screen.getByTestId('tv-tab-calendar').getAttribute('aria-selected')).toBe('true');
    expect(screen.queryByTestId('tv-import-badge')).toBeNull();
    fireEvent.click(screen.getByTestId('tv-tab-series'));
    expect(await screen.findByTestId(/^tv-series(-empty)?$/)).toBeTruthy();
    expect(screen.getByTestId('tv-section').getAttribute('data-view')).toBe('series');
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
    const drop = screen.getByTestId('tv-search-drop');
    expect(within(drop).getByTestId('tv-no-libraries')).toBeTruthy();
    fireEvent.click(within(drop).getByTestId('tv-choose-libraries'));
    expect(loc).toBe('/settings/tv?view=libraries');
    expect((screen.getByTestId('tv-search') as HTMLInputElement).value).toBe('');
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
