import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useMovieDetail } from '@/hooks/useMovieDetail';
import { _resetModalPrefetch } from '@/services/modalPrefetch';
import { _resetModalColors } from '@/services/modalColors';

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });

// An owned search result now carries tmdb_id, so the modal starts the Plex
// detail and the TMDb detail together instead of one after the other.
describe('owned search result with a tmdb_id', () => {
  let releasePlex: (tmdb: number | null) => void = () => {};
  beforeEach(() => { _resetModalPrefetch(); _resetModalColors(); });
  afterEach(() => { vi.unstubAllGlobals(); });

  const stub = () => {
    const plex = new Promise<number | null>((res) => { releasePlex = res; });
    const f = vi.fn(async (u: string) => {
      const url = String(u);
      if (url.includes('/api/plex/movie/7')) {
        const tmdb = await plex;
        return json({ title: 'Interstellar', year: 2014, tmdb_id: tmdb, cast: [] });
      }
      if (url.includes('/api/movie/157336')) return json({ id: 157336, backdrop_path: '/hint.jpg', videos: { results: [] } });
      if (url.includes('/api/movie/999')) return json({ id: 999, backdrop_path: '/right.jpg', videos: { results: [] } });
      if (url.includes('/api/clearlogo/')) return json({ clearlogo_url: null });
      return json({});
    });
    vi.stubGlobal('fetch', f);
    return f;
  };
  const called = (f: ReturnType<typeof vi.fn>, part: string) => f.mock.calls.some((c) => String(c[0]).includes(part));

  it('starts /api/movie while /api/plex/movie is still pending', async () => {
    const f = stub();
    const { result } = renderHook(() => useMovieDetail(7, 157336));
    await waitFor(() => expect(called(f, '/api/plex/movie/7')).toBe(true));
    expect(called(f, '/api/movie/157336')).toBe(true);
    releasePlex(157336);
    await waitFor(() => expect(result.current.detail?.tmdbId).toBe(157336));
    expect(result.current.detail?.backdropUrl).toContain('hint.jpg');
  });

  it('drops the early TMDb answer when Plex rejects the id', async () => {
    const f = stub();
    const { result } = renderHook(() => useMovieDetail(7, 157336));
    await waitFor(() => expect(called(f, '/api/movie/157336')).toBe(true));
    releasePlex(null);
    await waitFor(() => expect(result.current.detail?.logoSettled).toBe(true));
    expect(result.current.detail?.tmdbId).toBeUndefined();
    expect(result.current.detail?.backdropUrl ?? '').not.toContain('hint.jpg');
    expect(called(f, '/api/clearlogo/157336')).toBe(false);
  });

  it('uses the id Plex validated when it differs from the hint', async () => {
    const f = stub();
    const { result } = renderHook(() => useMovieDetail(7, 157336));
    await waitFor(() => expect(called(f, '/api/movie/157336')).toBe(true));
    releasePlex(999);
    await waitFor(() => expect(result.current.detail?.tmdbId).toBe(999));
    await waitFor(() => expect(result.current.detail?.backdropUrl).toContain('right.jpg'));
    expect(called(f, '/api/clearlogo/157336')).toBe(false);
  });
});
