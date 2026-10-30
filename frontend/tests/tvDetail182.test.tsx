/** v182: the series detail page (header, seasons newest first, episodes with the status edge, monitored bookmarks
 * that never search, Search, Interactive search and its grab, the v184 layout), and how it is opened. */
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
    { title: 'Severance.S02E02.1080p.WEB-DL-B', tracker: 'MyTracker', size: 2 * 1024 ** 3, seeders: 40, leechers: 1, quality: '1080p-web', quality_label: '1080p WEB', published: null,
      freeleech: false, download_url: '/api/trackers/release/' + 'x' * 20, season: 2, episodes: [{ id: 22, season: 2, episode: 2 }], pack: false, rejections: [], grabbable: true },
    { title: 'Severance.S02E01.2160p.WEB-DL-C', tracker: 'MyTracker', size: 8 * 1024 ** 3, seeders: 9, leechers: 0, quality: '2160p-web', published: null,
      freeleech: false, download_url: '/api/trackers/release/' + 'y' * 20, season: 2, episodes: [{ id: 21, season: 2, episode: 1 }], pack: false,
      rejections: ['Already has a file', 'Quality not in the profile'], grabbable: false },
  ],
};
const calls: { url: string; method: string; body: unknown }[] = [];
let grabOk = true;
let grabsAllowed = true;
let monSearch: unknown;
beforeEach(() => {
  calls.length = 0; grabOk = true; grabsAllowed = true; monSearch = undefined;
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
    if (url.endsWith('/monitored') && method === 'PUT') return res({ ...DETAIL, seasons: [], episodes: [], updated: 1, search: monSearch });
    if (url === '/api/settings/tv/shows/1/monitor-search') return res({ started: 30 });
    if (url === '/api/settings/tv/shows/1/search') return res({ ok: true, wanted: 1, searched: 1, results: 3, picks: [{ title: 'x', quality: null, episodes: [22], grabbed: true, error: null }], grabs_allowed: true, error: null });
    if (url === '/api/settings/tv/shows/1/interactive') return res({ ...RELEASES, grabs_allowed: grabsAllowed });
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
  it('the header: poster, title, year, network, status, quality profile, path, size, have/total, the monitored bookmark and Edit', async () => {
    page();
    expect((await screen.findByTestId('tv-detail-title')).textContent).toBe('Severance');
    expect(screen.getByTestId('tv-detail-meta').textContent).toBe('2022 \u00b7 Apple TV+ \u00b7 Returning Series');   // v321: TMDB casing
    const facts = screen.getByTestId('tv-detail-facts');
    expect(within(facts).getByTestId('tv-detail-fact-profile').textContent).toBe('Quality profileHD-1080p');
    expect(within(facts).getByTestId('tv-detail-fact-path').textContent).toBe('Path/media/TV/Severance (2022)');
    expect(within(facts).getByTestId('tv-detail-fact-size').textContent).toBe('Size5.0 GB');
    expect(within(facts).getByTestId('tv-detail-fact-episodes').textContent).toBe('Episodes2 of 4');
    const flag = screen.getByTestId('tv-detail-monitored');                     // v183: a bookmark, not a switch
    expect([flag.tagName, flag.getAttribute('aria-pressed'), flag.getAttribute('aria-label')]).toEqual(['BUTTON', 'true', 'Monitored']);
    expect(flag.querySelector('path')!.closest('svg')!.getAttribute('fill')).toBe('currentColor');
    expect(screen.queryAllByRole('switch')).toEqual([]);
    expect(screen.queryByTestId('tv-detail-poster')).toBeNull();                // v321: no poster
    fireEvent.click(screen.getByTestId('tv-detail-edit'));
    expect(await screen.findByTestId('tv-edit-series')).toBeTruthy();
  });

  it('seasons newest first, the newest open; collapsible; each with have/total, a monitored bookmark, Search and Interactive search', async () => {
    page();
    const seasons = await screen.findAllByTestId('tv-detail-season');
    expect(seasons.map(s => s.getAttribute('data-season'))).toEqual(['2', '1']);
    expect(seasons.map(s => s.getAttribute('data-open'))).toEqual(['true', null]);
    expect(within(seasons[0]!).getByTestId('tv-detail-season-count').textContent).toBe('1 of 2');
    const flags = seasons.map(x => within(x).getAllByTestId('tv-detail-season-monitored')[0]!);
    expect(flags.map(f => [f.getAttribute('aria-pressed'), f.getAttribute('aria-label'), f.querySelector('svg')!.getAttribute('fill')]))
      .toEqual([['true', 'Monitored', 'currentColor'], ['false', 'Monitored', 'none']]);
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
    expect(within(e21).getByTestId('tv-detail-air').textContent).toBe('Jan 16, 2025, 9:00 PM');
    expect(rows.map(r => within(r).getByTestId('tv-detail-status').textContent)).toEqual(['Unaired', 'Downloading', 'In Library']);
    const edge = within(e21).getByTestId('tv-detail-edge');
    expect([edge.style.width, edge.style.background]).toEqual(['4px', 'var(--tv-state-downloaded, #22c55e)']);
    expect(within(rows[1]!).getByTestId('tv-detail-edge').style.background).toBe('var(--tv-state-downloading, #3b82f6)');
    for (const r of rows) expect(within(r).getByTestId('tv-detail-status').querySelector('svg')).toBeNull();
    expect(document.querySelector('path[d="M20 6L9 17l-5-5"]')).toBeNull();                      // no checkmark anywhere
  });

  it('the monitored bookmarks (show, season, episode) flip on a click, write tv.db only and never search', async () => {
    page();
    await screen.findAllByTestId('tv-detail-episode');
    const season1 = screen.getAllByTestId('tv-detail-season').find(x => x.getAttribute('data-season') === '1')!;
    const ep23 = document.querySelector('[data-episode-id="23"]') as HTMLElement;
    const epFlag = within(ep23).getByTestId('tv-detail-episode-monitored');
    expect([epFlag.getAttribute('aria-pressed'), epFlag.getAttribute('aria-label'), epFlag.style.width, epFlag.style.height]).toEqual(['true', 'Monitored', '28px', '28px']);
    fireEvent.click(screen.getByTestId('tv-detail-monitored'));
    fireEvent.click(within(season1).getByTestId('tv-detail-season-monitored'));
    fireEvent.click(epFlag);
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

  it('v184 layout: no checkboxes or select-all; the show bookmark on the poster; season and episode bookmarks lead their rows; 1200px max width', async () => {
    page();
    await screen.findAllByTestId('tv-detail-episode');
    for (const id of ['tv-detail-select-all', 'tv-detail-count', 'tv-detail-check', 'tv-detail-season-check', 'tv-detail-bar'])
      expect(screen.queryByTestId(id)).toBeNull();
    expect(document.querySelectorAll('[role="checkbox"], input[type="checkbox"]').length).toBe(0);
    const show = screen.getByTestId('tv-detail-monitored');
    expect(screen.getByTestId('tv-detail-hero').contains(show)).toBe(true);   // v321: on the banner (no poster)
    expect([show.getAttribute('aria-label'), show.getAttribute('aria-pressed'), show.style.width]).toEqual(['Monitored', 'true', '40px']);
    expect(screen.getAllByTestId('tv-detail-monitored')).toHaveLength(1);
    for (const sec of screen.getAllByTestId('tv-detail-season')) {
      const head = sec.querySelector('.tvd-season-head')!;
      expect(head.querySelectorAll('[data-testid="tv-detail-season-monitored"]')).toHaveLength(1);
      expect(head.firstElementChild?.getAttribute('data-testid')).toBe('tv-detail-season-monitored');
    }
    for (const row of screen.getAllByTestId('tv-detail-episode')) {
      const kids = [...row.children].filter(k => k.getAttribute('data-testid') !== 'tv-detail-edge');
      expect(kids[0]?.getAttribute('data-testid')).toBe('tv-detail-episode-monitored');
      expect(row.querySelectorAll('[data-testid="tv-detail-episode-monitored"]')).toHaveLength(1);
    }
    const root = screen.getByTestId('tv-detail');
    expect([root.style.maxWidth, root.style.margin]).toEqual(['1200px', '0px auto']);
    fireEvent.click(show);
    await waitFor(() => expect(calls.some(c => c.method === 'PUT' && /\/shows\/1\/monitored$/.test(c.url))).toBe(true));
    expect(searches()).toEqual([]);
  });

  it('phone: the same page in one column (status and air date under the title)', async () => {
    page({ wide: false });
    const rows = await screen.findAllByTestId('tv-detail-episode');
    expect(within(rows[2]!).getByTestId('tv-detail-status').textContent).toBe('In Library');
    expect(within(rows[2]!).getByTestId('tv-detail-air').textContent).toBe(' \u00b7 Jan 16, 2025, 9:00 PM');
    expect(screen.getByTestId('tv-detail-edit')).toBeTruthy();
  });

  it('helpers', () => {
    // v185 (Designer): 'Nov 1, 9:00 PM': no year in the current year, no 'ET'
    const now = new Date('2026-10-18T12:00:00Z');
    expect(airET({ air_date: '2025-03-01' }, now)).toBe('Mar 1, 2025');
    expect(airET({ air_date: '2026-11-01' }, now)).toBe('Nov 1');
    expect(airET({ airstamp: '2025-07-04T01:00:00Z' }, now)).toBe('Jul 3, 2025, 9:00 PM');
    expect(airET({ airstamp: '2026-11-02T02:00:00Z' }, now)).toBe('Nov 1, 9:00 PM');
    expect(airET({}, now)).toBe('');
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

  it('v184: a top tab always opens its root, never the remembered detail page', async () => {
    render(<MemoryRouter initialEntries={['/settings/tv?view=series&show=1&episode=22']}><TvLibrarySettings wide /><Where /></MemoryRouter>);
    await screen.findByTestId('tv-detail-title');
    for (const [tab, want] of [['calendar', '/settings/tv'], ['series', '/settings/tv?view=series'], ['import', '/settings/tv?view=import'],
      ['libraries', '/settings/tv?view=libraries'], ['options', '/settings/tv?view=options'], ['series', '/settings/tv?view=series']] as const) {
      const btn = screen.queryByTestId(`tv-tab-${tab}`);
      if (!btn) continue;
      fireEvent.click(btn);
      expect(loc).toBe(want);
    }
    expect(screen.queryByTestId('tv-detail')).toBeNull();
  });

  it('v184: the week grid hides unmonitored episodes (episode, season or show), at once on the card bookmark', async () => {
    const mk = (id: number, extra: Partial<CalEpisode> = {}): CalEpisode => ({ episode_id: id, series_id: 1, show: 'Severance', year: 2022, poster_path: null,
      season: 2, episode: id, title: `Episode ${id}`, air_date: '2026-10-15', downloaded: false, monitored: true, ...extra });
    const eps = [mk(1), mk(2), mk(3, { downloaded: true }), mk(4, { monitored: false, downloaded: true })];
    const { rerender } = render(<WeekGrid week="2026-10-11" today="2026-10-17" eps={eps} />);
    const ids = () => screen.queryAllByTestId('tv-agenda-row').map(r => r.getAttribute('aria-label'));
    expect(ids()).toHaveLength(3);                                          // the unmonitored downloaded one is hidden
    // the episode's own bookmark: gone at once, and only a PUT to its monitored route (no search)
    fireEvent.click(within(screen.getAllByTestId('tv-agenda-row')[0]!).getByTestId('tv-card-bookmark'));
    expect(ids()).toHaveLength(2);
    await waitFor(() => expect(calls.filter(c => c.method === 'PUT').map(c => c.url)).toEqual(['/api/settings/tv/episodes/1/monitored']));
    // a season or the show unmonitored elsewhere: the next quiet answer lists them unmonitored, and they go
    rerender(<WeekGrid week="2026-10-11" today="2026-10-17" eps={[mk(1, { monitored: false }), mk(2, { monitored: false }), mk(3, { downloaded: true }), eps[3]!]} />);
    expect(ids()).toHaveLength(1);
    rerender(<WeekGrid week="2026-10-11" today="2026-10-17" eps={eps.map(e => ({ ...e, monitored: false }))} />);
    expect(ids()).toHaveLength(0);
    expect(searches()).toEqual([]);
  });

  it('v184: a bookmark ON that would queue more than 25 episode searches asks first, and searches only when confirmed', async () => {
    monSearch = { confirm: 30 };
    const say = page();
    await screen.findAllByTestId('tv-detail-season');
    const s1 = screen.getAllByTestId('tv-detail-season')[1]!;
    fireEvent.click(within(s1).getByTestId('tv-detail-season-monitored'));
    const dlg = await screen.findByTestId('tv-monitor-search-confirm');
    expect(dlg.textContent).toContain('Search 30 episodes?');
    expect(calls.filter(c => c.url.endsWith('/monitor-search'))).toEqual([]);
    fireEvent.click(screen.getByTestId('tv-monitor-search-cancel'));
    expect(screen.queryByTestId('tv-monitor-search-confirm')).toBeNull();
    expect(calls.filter(c => c.url.endsWith('/monitor-search'))).toEqual([]);
    fireEvent.click(within(screen.getAllByTestId('tv-detail-season')[1]!).getByTestId('tv-detail-season-monitored'));
    fireEvent.click(await screen.findByTestId('tv-monitor-search-go'));
    await waitFor(() => expect(calls.filter(c => c.url.endsWith('/monitor-search')).map(c => c.body)).toEqual([{ season: 1, confirm: true }]));
    await waitFor(() => expect(say).toHaveBeenCalledWith('Searching 30 episodes'));
  });
});

describe('v185 Designer polish', () => {
  it('the poster bookmark: a 40px dark glass chip, 22px glyph, 8px inside the top-left corner; filled white when monitored', async () => {
    page();
    const flag = await screen.findByTestId('tv-detail-monitored');
    // v321: no poster; the chip sits on the banner, top right beside Edit
    expect(flag.closest('[data-testid="tv-detail-hero"]')).toBeTruthy();
    expect([flag.style.width, flag.style.height]).toEqual(['40px', '40px']);
    const svg = flag.querySelector('svg')!;
    expect([svg.getAttribute('width'), svg.getAttribute('height')]).toEqual(['22', '22']);
    expect(flag.getAttribute('aria-label')).toBe('Monitored');
    expect(flag.getAttribute('aria-pressed')).toBe(String(DETAIL.monitored));
    expect(svg.getAttribute('fill')).toBe(DETAIL.monitored ? 'currentColor' : 'none');
  });

  it('Interactive search with grabs off: no notice line and no Grab buttons (like the search chips); one quality format', async () => {
    grabsAllowed = false;
    page();
    await screen.findAllByTestId('tv-detail-episode');
    fireEvent.click(screen.getByRole('button', { name: 'Interactive search S02E02 Episode 2' }));
    const modal = await screen.findByTestId('tv-interactive');
    const rows = await within(modal).findAllByTestId('tv-interactive-row');
    expect(within(modal).queryByText(/Grabs are off/)).toBeNull();
    expect(within(modal).queryByTestId('tv-interactive-off')).toBeNull();
    expect(within(modal).queryAllByTestId('tv-interactive-grab')).toHaveLength(0);
    expect(rows[0]!.textContent).toContain('1080p WEB');
    expect(rows[0]!.textContent).not.toContain('1080p-web');
  });

  it('the detail page has no checkboxes and no selected-count bar', async () => {
    page();
    await screen.findAllByTestId('tv-detail-episode');
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    expect(screen.queryByText(/selected/i)).toBeNull();
  });
});


describe('v187 phone detail and episode sheet', () => {
  it('phone rows read "n · Title" with a ⋯ that opens the sheet; the scrim closes it; Monitored toggles', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q === '(max-width: 767px)', addEventListener() {}, removeEventListener() {} }));
    render(<MemoryRouter><TvSeriesDetail seriesId={1} wide={false} onBack={() => {}} say={() => {}} /></MemoryRouter>);
    await screen.findByTestId('tv-detail-backrow');
    expect(screen.getByTestId('tv-detail-back').textContent).toBe('Series');     // v321: an 18px chevron icon
    expect(screen.getByTestId('tv-detail-back-chevron')).toBeTruthy();
    expect(screen.queryAllByTestId('tv-detail-episode-search')).toHaveLength(0);
    const titles = screen.getAllByTestId('tv-detail-episode-title').map(t => t.textContent);
    expect(titles).toContain('1 \u00b7 Hello, Ms. Cobel');
    fireEvent.click(screen.getAllByTestId('tv-detail-episode-menu')[0]!);
    const sheet = await screen.findByTestId('tv-episode-sheet');
    expect(within(sheet).getByText('Search')).toBeTruthy();
    expect(within(sheet).getByText('Interactive search')).toBeTruthy();
    fireEvent.click(screen.getByTestId('tv-episode-sheet-scrim'));
    expect(screen.queryByTestId('tv-episode-sheet')).toBeNull();
    fireEvent.click(screen.getAllByTestId('tv-detail-episode-menu')[0]!);
    fireEvent.click(screen.getByTestId('tv-sheet-monitor'));
    await waitFor(() => expect(calls.some(c => c.url.endsWith('/monitored') && c.method === 'PUT')).toBe(true));
    vi.unstubAllGlobals();
  });
});

describe('v196 phone interactive sheet', () => {
  it('opens full screen from the episode sheet, sorts, expands one row, and a grab closes it', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q === '(max-width: 767px)', addEventListener() {}, removeEventListener() {} }));
    render(<MemoryRouter><TvSeriesDetail seriesId={1} wide={false} onBack={() => {}} say={() => {}} /></MemoryRouter>);
    await screen.findByTestId('tv-detail-backrow');
    fireEvent.click(screen.getAllByTestId('tv-detail-episode-menu')[0]!);
    fireEvent.click(screen.getByTestId('tv-sheet-interactive'));
    const sheet = await screen.findByTestId('tv-interactive');
    expect(sheet.getAttribute('role')).toBe('dialog');
    await within(sheet).findAllByTestId('tv-interactive-row');
    expect(within(sheet).getByTestId('tv-interactive-count').textContent).toMatch(/2 releases$/);
    const titles = () => within(sheet).getAllByTestId('tv-interactive-row').map(r => r.textContent || '');
    expect(titles()[0]).toContain('S02E02');            // seeders, descending
    fireEvent.click(within(sheet).getByTestId('tv-interactive-sort-size'));
    expect(titles()[0]).toContain('S02E01');
    const toggles = within(sheet).getAllByTestId('tv-interactive-toggle');
    fireEvent.click(toggles[1]!);
    expect(within(sheet).getAllByTestId('tv-interactive-grab')).toHaveLength(1);
    fireEvent.click(toggles[0]!);                        // one row open at a time
    expect(within(sheet).getAllByTestId('tv-interactive-grab')).toHaveLength(1);
    fireEvent.click(toggles[1]!);
    fireEvent.click(within(sheet).getByTestId('tv-interactive-grab'));
    await waitFor(() => expect(screen.queryByTestId('tv-interactive')).toBeNull());
    expect(calls.some(c => c.url.endsWith('/interactive/grab'))).toBe(true);
    vi.unstubAllGlobals();
  });
});
