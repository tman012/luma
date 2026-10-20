/** v201 (Taylor's pick E2): the phone episode page, the automatic search dialog and the interactive screen. */
process.env.TZ = 'America/New_York';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvEpisodePage, groupOf, qualityText } from '@/components/library/TvEpisodePage';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const DETAIL = {
  id: 1, title: 'Slow Horses', year: 2022, tmdb_id: 1, monitored: true, monitor: 'all', monitor_new_seasons: 'all', monitor_specials: false,
  season_folder: true, series_type: 'standard', profile_id: 1, path_id: 'p', folder: 'f', path: '/f', network: 'Apple TV+', status: 'Returning Series',
  overview: null, poster_path: null, backdrop_path: null, profile_name: 'HD-1080p', size: 0, have: 0, total: 1,
  seasons: [{ season: 5, monitored: true, episodes: 1, have: 0, total: 1, size: 0 }],
  episodes: [{ id: 56, season: 5, episode: 6, monitored: true, title: 'Hard Stop', air_date: '2026-10-18', airstamp: '2026-10-18T07:00:00Z', runtime: 52,
    has_file: false, state: 'missing', quality: null, size: null }],
};
const REL = (title: string, seeders: number, size: number, tracker: string, quality: string) => ({
  title, tracker, size, seeders, leechers: 0, quality, quality_label: null, published: null, freeleech: false, download_url: '/x/' + title,
  season: 5, episodes: [{ id: 56, season: 5, episode: 6 }], pack: false, rejections: [], grabbable: true });
let searchBody: unknown;
let pending: ((v: Response) => void) | null = null;
const calls: string[] = [];
beforeEach(() => {
  calls.length = 0; pending = null;
  searchBody = { ok: true, wanted: 1, searched: 1, results: 1, grabs_allowed: true, error: null,
    picks: [{ title: 'Slow.Horses.S05E06.1080p.ATVP.WEB-DL.DDP5.1.H.264-NTb', quality: '1080p-web', episodes: [56], grabbed: true, error: null, size: 2254857830 }] };
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u); calls.push(`${init?.method || 'GET'} ${url}`);
    if (url.endsWith('/shows/1/detail')) return res(DETAIL);
    if (url.endsWith('/shows/1/search')) return pending ? new Promise<Response>(r => { pending = r; }) : res(searchBody);
    if (url.endsWith('/shows/1/interactive')) return res({ ok: true, searched: 1, grabs_allowed: true, error: null, releases: [
      REL('Slow.Horses.S05E06.720p.WEB.h264-ETHEL', 72, 860e6, 'IPTorrents', '720p-web'),
      REL('Slow.Horses.S05E06.2160p.ATVP.WEB-DL.DV.HDR.H.265-FLUX', 121, 6.9e9, 'BeyondHD', '2160p-web'),
      REL('Slow.Horses.S05E06.1080p.ATVP.WEB-DL.DDP5.1.H.264-NTb', 388, 2.1e9, 'PrivateHD', '1080p-web')] });
    if (url.endsWith('/interactive/grab')) return res({ ok: true, existing: false, episodes: [56] });
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const page = () => render(<TvEpisodePage seriesId={1} episodeId={56} onBack={() => {}} />);

describe('E2 episode page', () => {
  it('shows show · code, the title, air time · runtime, Monitored and the two buttons', async () => {
    page();
    expect((await screen.findByTestId('tv-episode-title')).textContent).toBe('Hard Stop');
    expect(screen.getByText('Slow Horses \u00b7 S5E6')).toBeTruthy();
    expect(screen.getByTestId('tv-episode-meta').textContent).toBe('Oct 18, 3:00 AM \u00b7 52 min');
    expect(screen.getByText('Monitored')).toBeTruthy();
    expect(screen.getByTestId('tv-episode-auto').textContent).toBe('Search automatically');
    expect(screen.getByTestId('tv-episode-interactive').textContent).toBe('Interactive search');
  });
  it('Search automatically: searching, then Grabbed with the release, quality and size', async () => {
    page();
    fireEvent.click(await screen.findByTestId('tv-episode-auto'));
    const dlg = await screen.findByTestId('tv-auto-dialog');
    await waitFor(() => expect(dlg.getAttribute('data-state')).toBe('grabbed'));
    expect(screen.getByTestId('tv-auto-release').textContent).toBe('Slow.Horses.S05E06.1080p.ATVP.WEB-DL.DDP5.1.H.264-NTb');
    expect(within(dlg).getByText('1080p WEB')).toBeTruthy();
    expect(within(dlg).getByText('2.1 GB')).toBeTruthy();
    fireEvent.click(screen.getByTestId('tv-auto-ok'));
    expect(screen.queryByTestId('tv-auto-dialog')).toBeNull();
  });
  it('the searching state has Cancel; a late answer after Cancel changes nothing', async () => {
    pending = () => {};
    page();
    fireEvent.click(await screen.findByTestId('tv-episode-auto'));
    expect((await screen.findByTestId('tv-auto-title')).textContent).toBe('Searching indexers\u2026');
    const finish = pending!;
    fireEvent.click(screen.getByTestId('tv-auto-cancel'));
    expect(screen.queryByTestId('tv-auto-dialog')).toBeNull();
    finish(res(searchBody));
    await new Promise(r => setTimeout(r, 10));
    expect(screen.queryByTestId('tv-auto-dialog')).toBeNull();
  });
  it('nothing found: No releases found, with Interactive search and Close', async () => {
    searchBody = { ok: true, wanted: 1, searched: 1, results: 0, grabs_allowed: true, error: null, picks: [] };
    page();
    fireEvent.click(await screen.findByTestId('tv-episode-auto'));
    await waitFor(() => expect(screen.getByTestId('tv-auto-title').textContent).toBe('No releases found'));
    expect(screen.getByTestId('tv-auto-close')).toBeTruthy();
    fireEvent.click(screen.getByTestId('tv-auto-interactive'));
    expect(await screen.findByTestId('tv-interactive-screen')).toBeTruthy();
  });
  it('interactive: count, Seeders sort first, Size sort, the size · seeders · tracker · group line, Grab', async () => {
    page();
    fireEvent.click(await screen.findByTestId('tv-episode-interactive'));
    const rows = await screen.findAllByTestId('tv-interactive-row');
    expect(screen.getByTestId('tv-interactive-count').textContent).toBe('3 releases');
    expect(rows[0]!.textContent).toContain('-NTb');
    expect(within(rows[0]!).getByTestId('tv-interactive-line').textContent).toBe('2.0 GB \u00b7 388 seeders \u00b7 PrivateHD \u00b7 NTb');
    fireEvent.click(screen.getByTestId('tv-interactive-sort-size'));
    expect(screen.getAllByTestId('tv-interactive-row')[0]!.textContent).toContain('FLUX');
    fireEvent.click(within(screen.getAllByTestId('tv-interactive-row')[0]!).getByTestId('tv-interactive-grab'));
    await waitFor(() => expect(screen.getByTestId('tv-auto-dialog').getAttribute('data-state')).toBe('grabbed'));
    expect(calls.some(c => c.endsWith('/interactive/grab'))).toBe(true);
  });
  it('helpers', () => {
    expect(qualityText('1080p-web')).toBe('1080p WEB');
    expect(groupOf('Slow.Horses.S05E06.720p.WEB.h264-ETHEL')).toBe('ETHEL');
    expect(groupOf('No.Group.mkv')).toBe('');
  });
});
