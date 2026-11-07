/** Phone calendar: a show or episode-row tap opens that show's series page.
 * Desktop week cards still open the episode. Day controls and series-row actions do not navigate. */
process.env.TZ = 'America/New_York';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvCalendarSheet } from '@/components/calendar/TvCalendarSheet';
import { OpenShowContext, WeekGrid, _clearCalendarCache } from '@/components/library/TvCalendar';
import { PhoneDay, TvCalendar } from '@/components/library/TvCalendar';
import { TvLibrarySettings } from '@/components/library/TvLibrarySettings';
import { addDays, localDay, type CalEpisode, type Day } from '@/components/library/tvCalendarApi';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const TV = { id: 'p_0000aaaa', name: 'TV', path: '/media/TV', status: 'ok' };
const ep = (o: Partial<CalEpisode>): CalEpisode => ({
  episode_id: 22, series_id: 1, show: 'Severance', year: 2022, poster_path: null, season: 2, episode: 2,
  title: 'Hello', air_date: localDay(), downloaded: false, monitored: true, airstamp: null, ...o,
});
const DETAIL = {
  id: 1, title: 'Severance', year: 2022, tmdb_id: 1, monitored: true, monitor: 'all', monitor_new_seasons: 'all',
  monitor_specials: false, season_folder: true, series_type: 'standard', profile_id: 1, path_id: TV.id,
  folder: 'Severance (2022)', path: '/media/TV/Severance (2022)', network: 'Apple TV+', status: 'Returning Series',
  overview: null, poster_path: null, backdrop_path: null, profile_name: 'HD-1080p', size: 0, have: 0, total: 1,
  seasons: [{ season: 2, monitored: true, episodes: 1, have: 0, total: 1, size: 0 }],
  episodes: [{ id: 22, season: 2, episode: 2, monitored: true, title: 'Hello', air_date: '2026-11-07', airstamp: null,
    runtime: 50, has_file: false, state: 'missing', quality: null, size: null }],
};

let loc = '';
let navState: unknown = null;
function Where() {
  const l = useLocation();
  loc = l.pathname + l.search;
  navState = l.state;
  return null;
}

beforeEach(() => {
  _clearCalendarCache();
  loc = '';
  navState = null;
  sessionStorage.clear();
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u);
    const method = init?.method || 'GET';
    if (url === '/api/settings/tv') return res({ paths: [TV], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6 });
    if (url === '/api/settings/tv/libraries') return res({ selected: [TV.id], libraries: [{ ...TV, selected: true }] });
    if (url === '/api/settings/tv/library') return res({ shows: [], unmatched: [], scan: { running: false, error: null, last_scan: null } });
    if (url.startsWith('/api/settings/tv/calendar')) {
      const p = new URL(url, 'http://x').searchParams;
      const start = p.get('start')!;
      const end = addDays(start, Number(p.get('days') || '7'));
      const today = localDay();
      const episodes = [
        ep({ air_date: today, airstamp: `${today}T22:00:00Z` }),
        ep({ episode_id: 23, series_id: 2, show: 'Dark', episode: 1, title: 'Next', air_date: addDays(today, 1), airstamp: `${addDays(today, 1)}T22:00:00Z` }),
      ];
      return res({ start, days: Number(p.get('days')), libraries: 1, refreshed_at: null, refreshing: false,
        episodes: episodes.filter(e => e.air_date >= start && e.air_date < end) });
    }
    if (/\/shows\/\d+\/detail$/.test(url)) return res({ ...DETAIL, id: Number(url.match(/shows\/(\d+)/)?.[1] || 1), title: url.includes('/shows/2/') ? 'Dark' : 'Severance' });
    if (url.endsWith('/monitored') && method === 'PUT') return res({ ...DETAIL, seasons: [], episodes: [], updated: 1 });
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); });

const phoneMedia = () => vi.stubGlobal('matchMedia', (q: string) => ({
  matches: q === '(max-width: 767px)', media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
}));

describe('phone calendar opens the series', () => {
  const today = localDay() as Day;
  it('a show tap and an episode-row tap open the series, not an episode; modified clicks do not', () => {
    const open = vi.fn();
    render(<OpenShowContext.Provider value={open}><PhoneDay day={today} today={today} eps={[
      ep({ episode_id: 1, episode: 1, title: 'One' }),
      ep({ episode_id: 2, episode: 2, title: 'Two' }),
      ep({ episode_id: 3, series_id: 2, show: 'Dark', title: 'Other' }),
    ]} /></OpenShowContext.Provider>);
    const card = screen.getByText('Severance').closest('[data-testid="tv-agenda-row"]') as HTMLAnchorElement;
    const other = screen.getByText('Dark').closest('[data-testid="tv-agenda-row"]') as HTMLAnchorElement;
    expect(card.tagName).toBe('A');
    expect(card.getAttribute('href')).toBe('/settings/tv?view=series&show=1');
    expect(other.getAttribute('href')).toBe('/settings/tv?view=series&show=2');
    expect(card.getAttribute('role')).toBeNull();
    expect(card.style.maxWidth).toBe('100%');
    expect(card.style.minWidth).toBe('0px');
    expect(card.style.overflow).toBe('hidden');
    expect(card.style.boxSizing).toBe('border-box');
    expect(card.style.minHeight).toBe('44px');
    expect(card.style.width).toBe('100%');
    fireEvent.click(card, { ctrlKey: true });
    fireEvent.click(card, { metaKey: true });
    fireEvent.click(card, { shiftKey: true });
    fireEvent.click(card, { altKey: true });
    fireEvent.click(card, { button: 1 });
    expect(open).not.toHaveBeenCalled();
    fireEvent.click(within(card).getAllByTestId('tv-glass-ep')[1]!);
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith(1);
    fireEvent.click(other);
    expect(open).toHaveBeenLastCalledWith(2);
  });

  it('a show with no series id is not a link and does not open the detail popup', () => {
    const open = vi.fn();
    render(<OpenShowContext.Provider value={open}><PhoneDay day={today} today={today} eps={[ep({ series_id: 0 })]} /></OpenShowContext.Provider>);
    const card = screen.getByTestId('tv-agenda-row');
    expect(card.tagName).toBe('DIV');
    expect(card.getAttribute('href')).toBeNull();
    fireEvent.click(card);
    expect(open).not.toHaveBeenCalled();
  });

  it('day controls stay on the calendar and are 44px', async () => {
    const open = vi.fn();
    render(<OpenShowContext.Provider value={open}><TvCalendar today={today} week={addDays(today, -todayWeekday(today)) as Day} onWeek={() => {}} phone /></OpenShowContext.Provider>);
    const prev = await screen.findByTestId('tv-day-prev');
    const next = screen.getByTestId('tv-day-next');
    expect(prev.style.width).toBe('44px');
    expect(prev.style.height).toBe('44px');
    expect(next.style.width).toBe('44px');
    fireEvent.click(next);
    fireEvent.click(prev);
    expect(open).not.toHaveBeenCalled();
    expect(screen.queryByTestId('tv-detail')).toBeNull();
    expect(screen.queryByTestId('tv-episode-page')).toBeNull();
  });
});

describe('desktop calendar is unchanged', () => {
  it('a week card still opens that episode, and the bookmark does not', () => {
    const open = vi.fn();
    const e = ep({ air_date: '2026-10-15' });
    render(<OpenShowContext.Provider value={open}><WeekGrid week="2026-10-11" today="2026-10-17" eps={[e]} /></OpenShowContext.Provider>);
    const card = screen.getByTestId('tv-agenda-row');
    expect(card.tagName).toBe('DIV');
    expect(card.getAttribute('href')).toBeNull();
    fireEvent.click(card);
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith(1, 22);
    open.mockClear();
    fireEvent.click(within(card).getByTestId('tv-card-bookmark'));
    expect(open).not.toHaveBeenCalled();
  });
});

describe('phone sheet', () => {
  it('opens the series page, keeps row actions there, and Back returns the day and scroll', async () => {
    render(<TvCalendarSheet open onClose={() => {}} />);
    const show = await screen.findByText('Severance');
    const scroller = show.closest('[data-scroll-container]') as HTMLElement;
    let top = 0;
    Object.defineProperty(scroller, 'scrollTop', { configurable: true, get: () => top, set: (v: number) => { top = v; } });
    const next = screen.getByTestId('tv-day-next');
    fireEvent.click(next);
    expect(screen.queryByTestId('tv-detail')).toBeNull();
    const later = await screen.findByText('Dark');
    expect(screen.getByTestId('tv-day-title').textContent).not.toBe('Today');
    const day = screen.getByTestId('tv-day-title').textContent;
    top = 180;
    fireEvent.click(later.closest('[data-testid="tv-agenda-row"]')!);
    const series = await screen.findByTestId('tv-cal-series');
    expect(series.style.overflowX).toBe('hidden');
    expect(series.style.maxWidth).toBe('100%');
    expect(series.style.minWidth).toBe('0px');
    expect((await screen.findByTestId('tv-detail-title')).textContent).toBe('Dark');
    expect(screen.queryByTestId('tv-episode-page')).toBeNull();
    expect(scroller.style.overflowX).toBe('hidden');
    await waitFor(() => expect(top).toBe(0));
    const back = screen.getByTestId('tv-detail-back');
    expect(back.style.minHeight).toBe('44px');
    fireEvent.click(screen.getAllByTestId('tv-detail-episode-menu')[0]!);
    expect(await screen.findByTestId('tv-episode-sheet')).toBeTruthy();
    expect(screen.getByTestId('tv-detail')).toBeTruthy();
    expect(screen.queryByTestId('tv-episode-page')).toBeNull();
    fireEvent.click(screen.getByTestId('tv-episode-sheet-scrim'));
    fireEvent.click(back);
    await waitFor(() => expect(top).toBe(180));
    expect(screen.queryByTestId('tv-detail')).toBeNull();
    expect(screen.getByTestId('tv-day-title').textContent).toBe(day);
    expect(screen.getByText('Dark')).toBeTruthy();
  });
});

describe('phone settings calendar', () => {
  it('opens the series route without an episode and Back restores the day and scroll', async () => {
    phoneMedia();
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
    Object.defineProperty(window, 'scrollY', { configurable: true, get: () => 240 });
    render(<MemoryRouter initialEntries={['/settings/tv']}><TvLibrarySettings wide={false} /><Where /></MemoryRouter>);
    expect(await screen.findByText('Severance')).toBeTruthy();
    fireEvent.click(screen.getByTestId('tv-day-next'));
    await waitFor(() => expect(loc).toMatch(/day=\d{4}-\d{2}-\d{2}/));
    const day = loc;
    expect(screen.queryByTestId('tv-episode-page')).toBeNull();
    fireEvent.click(await screen.findByText('Dark'));
    await waitFor(() => expect(loc).toMatch(/view=series/));
    expect(loc).toMatch(/show=2/);
    expect(loc).not.toMatch(/episode=/);
    expect(navState).toEqual({ fromCalendar: true });
    expect(await screen.findByTestId('tv-detail-title')).toHaveProperty('textContent', 'Dark');
    expect(screen.queryByTestId('tv-episode-page')).toBeNull();
    const menu = screen.getAllByTestId('tv-detail-episode-menu')[0]!;
    expect(menu.style.width).toBe('44px');
    expect(menu.style.height).toBe('44px');
    fireEvent.click(menu);
    expect(await screen.findByTestId('tv-episode-sheet')).toBeTruthy();
    expect(loc).toMatch(/view=series/);
    fireEvent.click(screen.getByTestId('tv-episode-sheet-scrim'));
    fireEvent.click(screen.getByTestId('tv-detail-back'));
    await waitFor(() => expect(loc).toBe(day));
    expect(loc).not.toMatch(/view=series/);
    expect(scrollTo).toHaveBeenCalledWith(0, 240);
    expect(screen.getByText('Dark')).toBeTruthy();
  });

  it('desktop still opens the series at that episode', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    }));
    render(<MemoryRouter initialEntries={['/settings/tv']}><TvLibrarySettings wide /><Where /></MemoryRouter>);
    const card = await screen.findByText('Severance');
    const row = card.closest('[data-testid="tv-agenda-row"]') as HTMLElement;
    expect(row.tagName).toBe('DIV');
    fireEvent.click(row);
    await waitFor(() => expect(loc).toMatch(/view=series&show=1&episode=22/));
    expect(screen.queryByTestId('tv-episode-page')).toBeNull();
  });
});

function todayWeekday(d: Day): number {
  const [y = 1970, m = 1, dd = 1] = d.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, dd, 12)).getUTCDay();
}
