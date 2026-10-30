/** Banner backfill (PM): the series page reads GET /api/settings/tv/shows/<id>/detail; when that JSON carries
 * banner_url (as GET /api/settings/tv/shows/<id> now does too), the header shows the banner art. */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TvSeriesDetail } from '@/components/library/TvSeriesDetail';

const BANNER = 'https://artworks.thetvdb.com/banners/graphical/95396-g1.jpg';
const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const base = (banner: string | null) => ({
  id: 1, title: 'Severance', year: 2022, tmdb_id: 95396, monitored: true, monitor: 'all', monitor_new_seasons: 'all', monitor_specials: false,
  season_folder: true, series_type: 'standard', profile_id: 1, path_id: 'p_0000aaaa', folder: 'Severance (2022)', path: '/media/TV/Severance (2022)',
  banner_url: banner, network: 'Apple TV+', status: 'Returning Series', overview: null, poster_path: null, backdrop_path: null,
  profile_name: 'HD-1080p', size: 0, have: 0, total: 0, seasons: [], episodes: [],
});
let banner: string | null = BANNER;
const urls: string[] = [];
beforeEach(() => {
  urls.length = 0; banner = BANNER;
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u); urls.push(url);
    if (url === '/api/settings/tv/shows/1/detail' || url === '/api/settings/tv/shows/1') return res(base(banner));
    if (url === '/api/settings/tv/profiles') return res({ profiles: [], qualities: [] });
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('series banner end to end', () => {
  it('banner_url from the API renders the hero banner', async () => {
    render(<TvSeriesDetail seriesId={1} wide onBack={() => {}} say={vi.fn()} />);
    const img = await screen.findByTestId('tv-detail-art');
    expect(img.getAttribute('src')).toContain('host=tvdb');
    expect(decodeURIComponent(img.getAttribute('src') || '')).toContain('95396-g1.jpg');
    expect(urls).toContain('/api/settings/tv/shows/1/detail');
  });

  it('no banner_url: no art', async () => {
    banner = null;
    render(<TvSeriesDetail seriesId={1} wide onBack={() => {}} say={vi.fn()} />);
    await screen.findByText('Severance', { selector: 'h1, [data-testid="tv-detail-title"]' });
    expect(screen.queryByTestId('tv-detail-art')).toBeNull();
  });
});
