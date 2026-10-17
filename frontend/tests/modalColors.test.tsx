import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const calls: string[] = [];
let responses: Record<string, { delay: number; body: unknown }> = {};
vi.mock('@/apiClient', () => ({
  apiFetch: (url: string) => {
    calls.push(url);
    const r = responses[url];
    return new Promise((res, rej) => {
      if (!r) return rej(new Error('offline'));
      setTimeout(() => res({ ok: true, json: () => Promise.resolve(r.body) }), r.delay);
    });
  },
}));

import { modalColors, prefetchModalColors, _resetModalColors, TMDB_PREFER_MS } from '@/services/modalColors';
import { useModalColors, COLOR_WAIT_MS } from '@/hooks/useModalColors';

const TMDB = { topLeft: '#111111', topRight: '#222222' };
const PLEX = { topLeft: '#aaaaaa', topRight: '#bbbbbb' };
const flush = async (ms: number) => { await act(async () => { vi.advanceTimersByTime(ms); for (let i = 0; i < 6; i++) await Promise.resolve(); }); };

describe('detail popup color blur', () => {
  beforeEach(() => { vi.useFakeTimers(); calls.length = 0; responses = {}; _resetModalColors(); });
  afterEach(() => { vi.useRealTimers(); });

  it('a card hover warms the cache, so opening reuses it (one request per source)', async () => {
    responses = { '/api/tmdb/colors/5': { delay: 100, body: TMDB }, '/api/plex/ultrablur/9': { delay: 10, body: PLEX } };
    prefetchModalColors('movie', 9, 5);
    await flush(200);
    const { result } = renderHook(() => useModalColors('movie', 9, 5));
    await flush(0);
    expect(result.current).toEqual({ colors: TMDB, settled: true });
    expect(calls.sort()).toEqual(['/api/plex/ultrablur/9', '/api/tmdb/colors/5']);
  });

  it('prefers TMDb colors, falling back to Plex when TMDb is slow', async () => {
    responses = { '/api/tmdb/colors/5': { delay: TMDB_PREFER_MS + 500, body: TMDB }, '/api/plex/ultrablur/9': { delay: 10, body: PLEX } };
    let got: unknown = 'pending';
    void modalColors('movie', 9, 5).then(c => { got = c; });
    await flush(TMDB_PREFER_MS + 10);
    expect(got).toEqual(PLEX);
  });

  it('settles before the popup shows, and drops a palette that arrives after the wait', async () => {
    responses = { '/api/tmdb/colors/7': { delay: COLOR_WAIT_MS + 300, body: TMDB } };
    const { result } = renderHook(() => useModalColors('tv', undefined, 7));
    expect(result.current.settled).toBe(false);
    await flush(COLOR_WAIT_MS + 10);
    expect(result.current).toEqual({ colors: null, settled: true });
    await flush(400);
    expect(result.current.colors).toBeNull(); // no late pop-in
  });

  it('a failed palette is not cached, so the next opening retries', async () => {
    let got: unknown = 'pending';
    void modalColors('movie', undefined, 3).then(c => { got = c; });
    await flush(10);
    expect(got).toBeNull();
    responses = { '/api/tmdb/colors/3': { delay: 5, body: TMDB } };
    void modalColors('movie', undefined, 3).then(c => { got = c; });
    await flush(20);
    expect(got).toEqual(TMDB);
  });
});
