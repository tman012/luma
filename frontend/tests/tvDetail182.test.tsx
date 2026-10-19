/** v182: the series detail page (header, seasons newest first, episodes with the status edge, monitor switches
 * that never search, Search, Interactive search and its grab, the episode checkboxes), and how it is opened. */
process.env.TZ = 'America/New_York';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvSeriesDetail, airET, bytes } from '@/components/library/TvSeriesDetail';
import { OpenShowContext, WeekGrid } from '@/components/library/TvCalendar';
import { TvLibrarySettings } from '@/components/library/TvLibrarySettings';
import type { CalEpisode } from '@/components/library/tvCalendarApi';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const TV = { id: 'p_0000aaaa', name: 'TV', path: '/media/TV', status: 'ok' };
const ep = (id: number, season: number, episode: number, state: string, extra: Record<string, unknown> = {}) => ({
  id, season, episode, monitored: true, title: `Episode ${episode}`, air_date: '2025-01-17', airstamp: null, runtime: 50,
  has_file: state === 'downloaded', state, quality: state === 'downloaded' ? '1080p WEB' : null, size: state === 'downloaded' ? 2 * 1024 ** 3 : null, ...extra,
});
const DETAIL = {
  id: 1, title: 'Severance', year: 2022, tmdb_id: 95396, monitored: true, monitor: 'all', monitor_new_seasons: 'all', monitor_specials: false,
  season_folder: true, series_type: 'standard', profile_id: 1, path_id: TV.id, folder: 'Severance (2022)', path: '/media/TV/Severance (2022)',
  network: 'Apple TV+', status: 'Returning Series', overview: null, poster_path: null, backdrop_path: null, profile_name: 'HD-1080p',
  size: 5 * 1024 ** 3, have: 2, total: 4,
  seasons: [{ season: 2, monitored: true, episodes: 3, have: 1, total: 2, size: 2 * 1024 ** 3 },
    { season: 1, monitored: false, episodes: 2, have: 1, total: 2, size: 3 * 1024 ** 3 }],
  episodes: [ep(11, 1, 1, 'downloaded', { monitored: false }), ep(12, 1, 2, 'missing', { monitored: false }),
    ep(21, 2, 1, 'downloaded', { airstamp: '2025-01-17T02:00:00Z', title: 'Hello, Ms. Cobel' }), ep(22, 2, 2, 'downloading'),
    ep(23, 2, 3, 'unaired', { air_date: '2099-01-01' })],
};
const RELEASES = {
  ok: true, searched: 1, grabs_allowed: true, error: null, releases: [
    { title: 'Severance.S02E02.1080p.WEB-DL-B', tracker: 'MyTracker', size: 2 * 1024 ** 3, seeders: 40, leechers: 1, quality: '1080p-web', published: null,
      freeleech: false, download_url: '/api/trackers/release/' + 'x' * 20, season: 2, episodes: [{ id: 22, season: 2, episode: 2 }], pack: false, rejections: [], grabbable: true },
    { title: 'Severance.S02E01.2160p.WEB-DL-C', tracker: 'MyTracker', size: 8 * 1024 ** 3, seeders: 9, leechers: 0, quality: '2160p-web', published: null,
      freeleech: false, download_url: '/api/trackers/release/' + 'y' * 20, season: 2, episodes: [{ id: 21, season: 2, episode: 1 }], pack: false,
      rejections: ['Already has a file', 'Quality not in the profile'], grabbable: false },
  ],
};
const calls: { url: string; method: string; body: unknown }[] = [];
let grabOk = true;
beforeEach(() => {
  calls.length = 0; grabOk = true;
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u); const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url === '/api/settings/tv') return res({ paths: [TV], upgrades: { mode: 'keep_both' }, rescan_interval_h: 6 });
    if (url === '/api/settings/tv/libraries') return res({ selected: [TV.id], libraries: [{ ...TV, selected: true }] });
    if (url === '/api/settings/tv/library') return res({ shows: [], unmatched: [], scan: { running: false, error: null, last_scan: null } });
    if (url === '/api/settings/tv/shows/1/detail') return res(DETAIL);
    if (url === '/api/settings/tv/shows/1' && method === 'GET') return res({ ...DETAIL, seasons: [], episodes: [] });
    if (url === '/api/settings/tv/profiles') return res({ profiles: [], qualities: [] });
    if (url.endsWith('/monitored') && method === 'PUT') return res({ ...DETAIL, seasons: [], episodes: [], updated: 1 });
    if (url === '/api/settings/tv/shows/1/search') return res({ ok: true, wanted: 1, searched: 1, results: 3, picks: [{ title: 'x', quality: null, episodes: [22], grabbed: true, error: null }], grabs_allowed: true, error: null });
    if (url === '/api/settings/tv/shows/1/interactive') return res(RELEASES);
    if (url === '/api/settings/tv/shows/1/interactive/grab') return grabOk ? res({ ok: true, existing: false, episodes: [22] }) : res({ ok: false, error: 'Not grabbed: already has a file', refused: 'already has a file' });
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const searches = () => calls.filter(c => /\/(search|interactive)/.test(c.url));
const page = (props: Partial<Parameters<typeof TvSeriesDetail>[0]> = {}) => {
  const say = vi.fn();
  render(<TvSeriesDetail seriesId={1} wide onBack={() => {}} say={say} {...props} />);
  return say;
};

describe('Series detail page', () => {
  it('the header: poster, title, year, network, status, quality profile, path, size, have/total, the monitor switch and Edit', async () => {
    page();
    expect((await screen.findByTestId('tv-detail-title')).textContent).toBe('Severance');
    expect(screen.getByTestId('tv-detail-meta').textContent).toBe('2022 \u00b7 Apple TV+ \u00b7 Returning series');
    const facts = screen.getByTestId('tv-detail-facts');
    expect(within(facts).getByTestId('tv-detail-fact-profile').textContent).toBe('Quality profileHD-1080p');
    expect(within(facts).getByTestId('tv-detail-fact-path').textContent).toBe('Path/media/TV/Severance (2022)');
    expect(within(facts).getByTestId('tv-detail-fact-size').textContent).toBe('Size5.0 GB');
    expect(within(facts).getByTestId('tv-detail-fact-episodes').textContent).toBe('Episodes2 of 4');
    expect(screen.getByTestId('tv-detail-monitored-toggle').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('tv-poster-tile')).toBeTruthy();
    fireEvent.click(screen.getByTestId('tv-detail-edit'));
    expect(await screen.findByTestId('tv-edit-series')).toBeTruthy();
  });

  it('seasons newest first, the newest open; collapsible; each with have/total, a monitor switch, Search and Interactive search', async () => {
    page();
    const seasons = await screen.findAllByTestId('tv-detail-season');
    expect(seasons.map(s => s.getAttribute('data-season'))).toEqual(['2', '1']);
    expect(seasons.map(s => s.getAttribute('data-open'))).toEqual(['true', null]);
    expect(within(seasons[0]!).getByTestId('tv-detail-season-count').textContent).toBe('1 of 2');
    expect(within(seasons[0]!).getByRole('switch', { name: 'Monitor Season 2' }).getAttribute('aria-checked')).toBe('true');
    expect(within(seasons[1]!).getByRole('switch', { name: 'Monitor Season 1' }).getAttribute('aria-checked')).toBe('false');
    expect(within(seasons[0]!).getByRole('button', { name: 'Search Season 2' })).toBeTruthy();
    expect(within(seasons[0]!).getByRole('button', { name: 'Interactive search Season 2' })).toBeTruthy();
    expect(within(seasons[0]!).getAllByTestId('tv-detail-episode').map(e => e.getAttribute('data-episode-id'))).toEqual(['23', '22', '21']);
    fireEvent.click(within(seasons[1]!).getByTestId('tv-detail-season-toggle'));
    expect(within(screen.getAllByTestId('tv-detail-season')[1]!).getAllByTestId('tv-detail-episode')).toHaveLength(2);
    fireEvent.click(within(screen.getAllByTestId('tv-detail-season')[0]!).getByTestId('tv-detail-season-toggle'));
    expect(within(screen.getAllByTestId('tv-detail-season')[0]!).queryAllByTestId('tv-detail-episode')).toHaveLength(0);
  });

  it('episodes: number, title, air date in ET, status as the 4px edge and a word (no checkmarks)', async () => {
    page();
    await screen.findAllByTestId('tv-detail-episode');
    const rows = screen.getAllByTestId('tv-detail-episode');
    const e21 = rows.find(r => r.getAttribute('data-episode-id') === '21')!;
    expect(within(e21).getByTestId('tv-detail-episode-title').textContent).toBe('Hello, Ms. Cobel');
    expect(within(e21).getByTestId('tv-detail-air').textContent).toBe('Jan 16, 2025, 9:00 PM ET');
    expect(rows.map(r => within(r).getByTestId('tv-detail-status').textContent)).toEqual(['Unaired', 'Downloading', 'Downloaded']);
    const edge = within(e21).getByTestId('tv-detail-edge');
    expect([edge.style.width, edge.style.background]).toEqual(['4px', 'var(--tv-state-downloaded, #22c55e)']);
    expect(within(rows[1]!).getByTestId('tv-detail-edge').style.background).toBe('rgb(245, 158, 11)');
    for (const r of rows) expect(within(r).getByTestId('tv-detail-status').querySelector('svg')).toBeNull();
    expect(document.querySelector('path[d="M20 6L9 17l-5-5"]')).toBeNull();                      // no checkmark anywhere
  });

  it('monitor switches (show, season, episode) write tv.db only and never search', async () => {
    page();
    await screen.findAllByTestId('tv-detail-episode');
    fireEvent.click(screen.getByTestId('tv-detail-monitored-toggle'));
    fireEvent.click(screen.getByRole('switch', { name: 'Monitor Season 1' }));
    fireEvent.click(screen.getByRole('switch', { name: 'Monitor S02E03 Episode 3' }));
    await waitFor(() => expect(calls.filter(c => c.method === 'PUT')).toHaveLength(3));
    expect(calls.filter(c => c.method === 'PUT').map(c => [c.url, c.body])).toEqual([
      ['/api/settings/tv/shows/1/monitored', { monitored: false }],
      ['/api/settings/tv/shows/1/seasons/1/monitored', { monitored: true }],
      ['/api/settings/tv/episodes/23/monitored', { monitored: false }],
    ]);
    expect(searches()).toEqual([]);
  });

  it('Search: a season or an episode, with the outcome', async () => {
    const say = page();
    await screen.findAllByTestId('tv-detail-episode');
    fireEvent.click(screen.getByRole('button', { name: 'Search Season 2' }));
    await waitFor(() => expect(say).toHaveBeenCalledWith('Season 2: Grabbed 1 release'));
    fireEvent.click(screen.getByRole('button', { name: 'Search S02E02 Episode 2' }));
    await waitFor(() => expect(say).toHaveBeenCalledWith('S02E02: Grabbed 1 release'));
    expect(searches().map(c => c.body)).toEqual([{ season: 2 }, { episode_id: 22 }]);
  });

  it('Interactive search lists releases with rejections; a rejected one cannot be grabbed; Grab sends the picked release', async () => {
    page();
    await screen.findAllByTestId('tv-detail-episode');
    fireEvent.click(screen.getByRole('button', { name: 'Interactive search S02E02 Episode 2' }));
    const modal = await screen.findByTestId('tv-interactive');
    const rows = await within(modal).findAllByTestId('tv-interactive-row');
    expect(calls.find(c => c.url.endsWith('/interactive'))!.body).toEqual({ episode_id: 22 });
    expect(within(modal).getByTestId('tv-interactive-count').textContent).toBe('2 releases');
    expect(within(rows[1]!).getByTestId('tv-interactive-rejections').textContent).toBe('Already has a file \u00b7 Quality not in the profile');
    expect((within(rows[1]!).getByTestId('tv-interactive-grab') as HTMLButtonElement).disabled).toBe(true);
    expect(rows[0]!.textContent).toContain('2.0 GB');
    fireEvent.click(within(rows[0]!).getByTestId('tv-interactive-grab'));
    await waitFor(() => expect(within(rows[0]!).getByTestId('tv-interactive-grab').textContent).toBe('Grabbed'));
    expect(calls.find(c => c.url.endsWith('/interactive/grab'))!.body).toEqual({
      download_url: '/api/trackers/release/' + 'x' * 20, title: 'Severance.S02E02.1080p.WEB-DL-B', size: 2 * 1024 ** 3, quality: '1080p-web', indexer: 'MyTracker' });
  });

  it('a refused grab says why', async () => {
    grabOk = false;
    page();
    await screen.findAllByTestId('tv-detail-episode');
    fireEvent.click(screen.getByRole('button', { name: 'Interactive search Season 2' }));
    const rows = await screen.findAllByTestId('tv-interactive-row');
    expect(calls.find(c => c.url.endsWith('/interactive'))!.body).toEqual({ season: 2 });
    fireEvent.click(within(rows[0]!).getByTestId('tv-interactive-grab'));
    expect((await screen.findByTestId('tv-interactive-grab-error')).textContent).toBe('Not grabbed: already has a file');
  });

  it('the episode checkboxes: per episode, per season, select all; Monitor selected never searches', async () => {
    page();
    await screen.findAllByTestId('tv-detail-episode');
    fireEvent.click(within(screen.getAllByTestId('tv-detail-season')[0]!).getByTestId('tv-detail-season-check'));
    expect(screen.getByTestId('tv-detail-bar-count').textContent).toBe('3 selected');
    expect(screen.getByTestId('tv-detail-select-all').getAttribute('aria-checked')).toBe('mixed');
    fireEvent.click(screen.getAllByTestId('tv-detail-check')[0]!);
    expect(screen.getByTestId('tv-detail-count').textContent).toBe('2 selected');
    fireEvent.click(screen.getByTestId('tv-detail-bar-monitor'));
    await waitFor(() => expect(calls.some(c => c.url === '/api/settings/tv/shows/1/episodes/monitored')).toBe(true));
    expect(calls.find(c => c.url === '/api/settings/tv/shows/1/episodes/monitored')!.body).toEqual({ episode_ids: [22, 21], monitored: true });
    await waitFor(() => expect(screen.queryByTestId('tv-detail-bar')).toBeNull());
    fireEvent.click(screen.getByTestId('tv-detail-select-all'));
    expect(screen.getByTestId('tv-detail-count').textContent).toBe('5 selected');
    expect(searches()).toEqual([]);
  });

  it('phone: the same page in one column (status and air date under the title)', async () => {
    page({ wide: false });
    const rows = await screen.findAllByTestId('tv-detail-episode');
    expect(within(rows[2]!).getByTestId('tv-detail-status').textContent).toBe('Downloaded');
    expect(within(rows[2]!).getByTestId('tv-detail-air').textContent).toBe(' \u00b7 Jan 16, 2025, 9:00 PM ET');
    expect(screen.getByTestId('tv-detail-edit')).toBeTruthy();
  });

  it('helpers', () => {
    expect(airET({ air_date: '2025-03-01' })).toBe('Mar 1, 2025');
    expect(airET({ airstamp: '2025-07-04T01:00:00Z' })).toBe('Jul 3, 2025, 9:00 PM ET');
    expect(airET({})).toBe('');
    expect([bytes(0), bytes(850 * 1024 ** 2), bytes(1.5 * 1024 ** 3)]).toEqual(['0 B', '850 MB', '1.5 GB']);
  });
});

let loc = '';
function Where() { const l = useLocation(); loc = l.pathname + l.search; return null; }

describe('Opening the page', () => {
  it('Settings > TV > Series ?show=<id> is the page; Series goes back', async () => {
    render(<MemoryRouter initialEntries={['/settings/tv?view=series&show=1']}><TvLibrarySettings wide /><Where /></MemoryRouter>);
    expect((await screen.findByTestId('tv-detail-title')).textContent).toBe('Severance');
    expect(screen.getByTestId('tv-section').getAttribute('data-view')).toBe('series');
    fireEvent.click(screen.getByTestId('tv-detail-back'));
    expect(loc).toBe('/settings/tv?view=series');
  });

  it('a calendar card opens its show on the page at that episode', () => {
    const open = vi.fn();
    const e: CalEpisode = { episode_id: 22, series_id: 1, show: 'Severance', year: 2022, poster_path: null, season: 2, episode: 2,
      title: 'Episode 2', air_date: '2026-10-15', downloaded: false };
    render(<OpenShowContext.Provider value={open}><WeekGrid week="2026-10-11" today="2026-10-17" eps={[e]} /></OpenShowContext.Provider>);
    fireEvent.click(screen.getByTestId('tv-agenda-row'));
    expect(open).toHaveBeenCalledWith(1, 22);
  });
});
