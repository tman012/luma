import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTitleReveal, logoInput, REVEAL_CAP_MS } from '@/hooks/useTitleReveal';
import { useMovieDetail } from '@/hooks/useMovieDetail';
import { detailFetch, warmDetail, _resetModalPrefetch } from '@/services/modalPrefetch';
import { _resetModalColors } from '@/services/modalColors';
import { logoImg } from '@/utils/image';

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });

describe('no logo reveals the text title at once', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('a settled null logo shows text immediately, without waiting for the cap', () => {
    const { result } = renderHook(() => useTitleReveal(null, 'Heat'));
    expect(result.current).toBe('text');
  });

  it('a pending (undefined) logo holds, and reveals text at the cap at the latest', async () => {
    const { result } = renderHook(() => useTitleReveal(undefined, 'Heat'));
    expect(result.current).toBe('hold');
    await act(async () => { await vi.advanceTimersByTimeAsync(REVEAL_CAP_MS); });
    expect(result.current).toBe('text');
  });

  it('logoInput tells "no logo" from "still loading"', () => {
    expect(logoInput(null)).toBeUndefined();
    expect(logoInput({ clearLogoUrl: null })).toBeUndefined();
    expect(logoInput({ clearLogoUrl: null, logoSettled: true })).toBeNull();
    expect(logoInput({ clearLogoUrl: '/l.png', logoSettled: false })).toBe('/l.png');
  });
});

describe('movie detail: one /api/movie request, settled no-logo', () => {
  beforeEach(() => { _resetModalPrefetch(); _resetModalColors(); });
  afterEach(() => { vi.unstubAllGlobals(); });

  const stub = () => {
    const f = vi.fn(async (u: string) => {
      const url = String(u);
      if (url.includes('/api/movie/42')) return json({ id: 42, title: 'Heat', overview: 'x', backdrop_path: null, videos: { results: [] } });
      if (url.includes('/api/clearlogo/42')) return json({ clearlogo_url: null });
      return json({});
    });
    vi.stubGlobal('fetch', f);
    return f;
  };
  const movieCalls = (f: ReturnType<typeof vi.fn>) => f.mock.calls.filter((c) => String(c[0]).includes('/api/movie/42')).length;

  it('parallel callers (prefetch + popup + enrichment) share one in-flight request', async () => {
    const f = stub();
    await Promise.all([warmDetail('movie', undefined, 42), detailFetch('/api/movie/42'), detailFetch('/api/movie/42')]);
    expect(movieCalls(f)).toBe(1);
  });

  it('a TMDb-only open fetches /api/movie once and marks the null logo as settled', async () => {
    const f = stub();
    const { result } = renderHook(() => useMovieDetail(undefined, 42));
    await waitFor(() => expect(result.current.detail?.logoSettled).toBe(true));
    expect(result.current.detail?.clearLogoUrl).toBeNull();
    expect(logoInput(result.current.detail)).toBeNull();
    expect(movieCalls(f)).toBe(1);
  });
});

describe('phone logo size', () => {
  afterEach(() => { (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = undefined; });
  it('uses w500 for TMDb and a 500px transcode for Plex logos on phones', () => {
    (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    Object.defineProperty(window, 'innerWidth', { value: 390, configurable: true });
    expect(logoImg('https://image.tmdb.org/t/p/original/a.png')).toContain('w500');
    expect(logoImg('/api/img/plex?url=/library/metadata/1/clearLogo')).toContain('w=500');
    Object.defineProperty(window, 'innerWidth', { value: 1440, configurable: true });
    expect(logoImg('/api/img/plex?url=/library/metadata/1/clearLogo')).not.toContain('w=500');
  });
});
