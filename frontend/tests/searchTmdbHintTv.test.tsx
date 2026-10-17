import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTvDetail } from '@/hooks/useTvDetail';
import { warmDetail, _resetModalPrefetch } from '@/services/modalPrefetch';
import { _resetModalColors } from '@/services/modalColors';

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });

// An owned show from search carries tmdb_id: /api/tv starts alongside
// /api/plex/tv, and the id Plex validates decides what shows.
describe('owned TV search result with a tmdb_id', () => {
  let releasePlex: (tmdb: number | null) => void = () => {};
  beforeEach(() => { _resetModalPrefetch(); _resetModalColors(); });
  afterEach(() => { vi.unstubAllGlobals(); });

  const stub = () => {
    const plex = new Promise<number | null>((res) => { releasePlex = res; });
    const f = vi.fn(async (u: string) => {
      const url = String(u);
      if (url.includes('/api/tv/seasons/')) return json({ success: true, tvdb_id: null, seasons: [] });
      if (url.includes('/api/plex/tv/9')) {
        const tmdb = await plex;
        return json({ title: 'Plex title', year: 2008, tmdb_id: tmdb, cast: [] });
      }
      if (url.includes('/api/tv/1396')) return json({ id: 1396, name: 'Hint show', backdrop_path: '/hint.jpg', videos: { results: [] } });
      if (url.includes('/api/tv/2000')) return json({ id: 2000, name: 'Right show', backdrop_path: '/right.jpg', videos: { results: [] } });
      if (url.includes('/api/clearlogo/')) return json({ clearlogo_url: null });
      return json({});
    });
    vi.stubGlobal('fetch', f);
    return f;
  };
  const count = (f: ReturnType<typeof vi.fn>, part: string) => f.mock.calls.filter((c) => String(c[0]).includes(part)).length;

  it('starts /api/tv while /api/plex/tv is still pending, and fetches it once', async () => {
    const f = stub();
    const { result } = renderHook(() => useTvDetail(9, 1396));
    await waitFor(() => expect(count(f, '/api/plex/tv/9')).toBe(1));
    expect(count(f, '/api/tv/1396')).toBe(1);
    releasePlex(1396);
    await waitFor(() => expect(result.current.show?.title).toBe('Hint show'));
    expect(result.current.show?.tmdbId).toBe(1396);
    expect(result.current.show?.isOwned).toBe(true);
    expect(result.current.resolvedTmdbId).toBe(1396);
    expect(count(f, '/api/tv/1396')).toBe(1);
  });

  it('shows the Plex data when Plex rejects the id', async () => {
    const f = stub();
    const { result } = renderHook(() => useTvDetail(9, 1396));
    await waitFor(() => expect(count(f, '/api/tv/1396')).toBe(1));
    releasePlex(null);
    await waitFor(() => expect(result.current.show?.title).toBe('Plex title'));
    expect(result.current.show?.tmdbId).toBeFalsy();
    expect(result.current.resolvedTmdbId).toBeUndefined();
    expect(count(f, '/api/clearlogo/1396')).toBe(0);
    expect(count(f, '/api/tv/seasons/1396')).toBe(0);
  });

  it('uses the id Plex validated when it differs from the hint', async () => {
    const f = stub();
    const { result } = renderHook(() => useTvDetail(9, 1396));
    await waitFor(() => expect(count(f, '/api/tv/1396')).toBe(1));
    releasePlex(2000);
    await waitFor(() => expect(result.current.show?.title).toBe('Right show'));
    expect(result.current.show?.tmdbId).toBe(2000);
    expect(result.current.resolvedTmdbId).toBe(2000);
    expect(count(f, '/api/clearlogo/1396')).toBe(0);
  });

  it('the card warm hits /api/plex/tv too, so the server-side type check is warm', async () => {
    const f = stub();
    releasePlex(1396);
    await warmDetail('tv', 9, 1396, { images: false });
    expect(count(f, '/api/plex/tv/9')).toBe(1);
    expect(count(f, '/api/tv/1396')).toBe(1);
  });
});
