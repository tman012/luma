import { describe, it, expect } from 'vitest';
import { tvIndexerQuery } from '@/components/media/TvDetailModal/useTvSourcesData';

describe('tvIndexerQuery', () => {
  it('adds SxxEyy for an episode and never the year', () => {
    expect(tvIndexerQuery('Breaking Bad', { type: 'episode', season: 1, episode: 5 })).toBe('Breaking Bad S01E05');
  });
  it('adds Sxx for a season', () => {
    expect(tvIndexerQuery('Breaking Bad', { type: 'season', season: 3 })).toBe('Breaking Bad S03');
  });
  it('is empty without a title', () => {
    expect(tvIndexerQuery('', { type: 'season', season: 1 })).toBe('');
  });
});

import { tvIndexerQueries, coversSeason } from '@/components/media/TvDetailModal/useTvSourcesData';

describe('season browse also catches multi-season packs (PM 4)', () => {
  it('asks for the season and the bare title; an episode asks once', () => {
    expect(tvIndexerQueries('Breaking Bad', { type: 'season', season: 1 })).toEqual(['Breaking Bad S01', 'Breaking Bad']);
    expect(tvIndexerQueries('Breaking Bad', { type: 'episode', season: 1, episode: 5 })).toEqual(['Breaking Bad S01E05']);
    expect(tvIndexerQueries('', { type: 'season', season: 1 })).toEqual([]);
  });
  it.each([
    ['Breaking.Bad.S01-S05.1080p.BluRay.x264-GRP', 1, true],
    ['Breaking.Bad.S01-S05.1080p.BluRay.x264-GRP', 3, true],
    ['Breaking.Bad.S01-S05.1080p.BluRay.x264-GRP', 6, false],
    ['Breaking Bad S01-05 2160p', 5, true],
    ['Breaking Bad Seasons 1-5 Complete 1080p', 2, true],
    ['Breaking.Bad.Complete.Series.1080p.BluRay-GRP', 4, true],
    ['Breaking.Bad.The.Complete.Collection.720p', 1, true],
    ['Breaking.Bad.S01.1080p.BluRay.x264-GRP', 1, true],
    ['Breaking.Bad.S02.1080p.BluRay.x264-GRP', 1, false],
    ['Breaking.Bad.S01E05.1080p.WEB-DL-GRP', 1, true],
    ['Breaking.Bad.S03E05.1080p.WEB-DL-GRP', 1, false],
    ['Breaking Bad 1x05 720p', 1, true],
    ['Breaking Bad Season 2 1080p', 2, true],
    ['Breaking.Bad.S01E01-E07.1080p', 1, true],
    ['Breaking.Bad.2008.1080p.Documentary', 1, false],
  ])('%s covers season %i: %s', (title, season, want) => {
    expect(coversSeason(title as string, season as number)).toBe(want);
  });
});

describe('season browse merges both answers and keeps only this season', () => {
  it('drops other seasons, keeps packs and episodes, dedupes', async () => {
    const { renderHook, waitFor } = await import('@testing-library/react');
    const { useTvSourcesData } = await import('@/components/media/TvDetailModal/useTvSourcesData');
    const { vi } = await import('vitest');
    const row = (title: string, id: string) => ({ title, guid: id, download_url: `/api/sources/link/${id}`, indexer: 'T', indexer_id: 1, size: 1, seeders: 5 });
    const byQ: Record<string, unknown[]> = {
      'Breaking Bad S01': [row('Breaking.Bad.S01.1080p.BluRay-GRP', 'a'), row('Breaking.Bad.S01E01.720p-GRP', 'b')],
      'Breaking Bad': [row('Breaking.Bad.S01.1080p.BluRay-GRP', 'a'), row('Breaking.Bad.S01-S05.1080p-GRP', 'c'),
        row('Breaking.Bad.S04E02.1080p-GRP', 'd'), row('Breaking.Bad.Complete.Series.2160p-GRP', 'e')],
    };
    const queries: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      const url = new URL(String(u), 'http://x');
      if (url.pathname.endsWith('/api/sources/all-results')) {
        const q = url.searchParams.get('title') || '';
        queries.push(q);
        expect(url.searchParams.get('year')).toBeNull();
        return new Response(JSON.stringify({ releases: byQ[q] || [] }), { status: 200 });
      }
      return new Response(JSON.stringify({ success: false }), { status: 503 });
    }));
    const opts = { target: { type: 'season', season: 1 }, tvdbId: 1, tmdbId: 1, title: 'Breaking Bad', year: 2008 } as never;
    const { result, unmount } = renderHook(() => useTvSourcesData(opts));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(queries.sort()).toEqual(['Breaking Bad', 'Breaking Bad S01']);
    expect(result.current.releases.map((r: { title: string }) => r.title).sort()).toEqual([
      'Breaking.Bad.Complete.Series.2160p-GRP', 'Breaking.Bad.S01-S05.1080p-GRP',
      'Breaking.Bad.S01.1080p.BluRay-GRP', 'Breaking.Bad.S01E01.720p-GRP']);
    unmount();
    vi.unstubAllGlobals();
  });
});
