/** cw-logo: a Continue Watching episode opens its show by the Plex key only; the show's clearlogo is looked up by the
 *  TMDb id Plex names. A failed lookup (5xx / network) is retried, not settled as "no logo" (text title). */
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTvDetail } from '@/hooks/useTvDetail';
import { openDetail } from '@/pages/MinimalHomePage';
import { _resetModalPrefetch } from '@/services/modalPrefetch';
import { _resetModalColors } from '@/services/modalColors';
import { logoInput } from '@/hooks/useTitleReveal';

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
const SHOW = 4501, TMDB = 6191, EP = 4591;

describe('Continue Watching → show sheet clearlogo', () => {
  beforeEach(() => { _resetModalPrefetch(); _resetModalColors(); });
  afterEach(() => { vi.unstubAllGlobals(); });
  const stub = (logoFails: number | 'net') => {
    let fails = 0;
    const f = vi.fn(async (u: string) => {
      const url = String(u);
      if (url.includes('/api/tv/seasons/')) return json({ success: true, tvdb_id: null, seasons: [] });
      if (url.includes(`/api/plex/tv/${SHOW}`)) return json({ title: 'Show', year: 1959, tmdb_id: TMDB, cast: [] });
      if (url.includes(`/api/tv/${TMDB}`)) return json({ id: TMDB, name: 'Show', backdrop_path: '/b.jpg', videos: { results: [] } });
      if (url.includes(`/api/clearlogo/${TMDB}`)) {
        if (logoFails === 'net' ? fails++ < 1 : fails++ < logoFails) {
          if (logoFails === 'net') throw new TypeError('Failed to fetch');
          return json({ clearlogo_url: null }, 500);
        }
        return json({ clearlogo_url: 'https://image.tmdb.org/t/p/original/logo.png' });
      }
      return json({});
    });
    vi.stubGlobal('fetch', f);
    return f;
  };
  const count = (f: ReturnType<typeof vi.fn>, part: string) => f.mock.calls.filter((c) => String(c[0]).includes(part)).length;

  it('the CW episode card opens the parent show by its Plex key', () => {
    const openTv = vi.fn();
    openDetail({ openMovie: vi.fn(), openTv }, { key: String(EP), show_key: String(SHOW), type: 'episode', season: 1, episode: 2, title: 'Ep' } as never);
    expect(openTv).toHaveBeenCalledWith(expect.objectContaining({ ratingKey: SHOW }));
  });

  for (const mode of [1, 2, 'net'] as const) {
    it(`a clearlogo lookup that fails (${mode}) is retried: the show settles with its logo, not text`, async () => {
      const f = stub(mode);
      const { result } = renderHook(() => useTvDetail(SHOW, undefined));
      await waitFor(() => expect(result.current.show?.logoSettled).toBe(true), { timeout: 2000 });
      expect(logoInput(result.current.show)).toMatch(/logo\.png/);
      expect(count(f, `/api/clearlogo/${TMDB}?type=tv`)).toBeGreaterThan(1);
    });
  }

  it('a real "no logo" answer settles as text at once (no retry)', async () => {
    const f = vi.fn(async (u: string) => {
      const url = String(u);
      if (url.includes(`/api/plex/tv/${SHOW}`)) return json({ title: 'Show', tmdb_id: TMDB, cast: [] });
      if (url.includes(`/api/tv/${TMDB}`)) return json({ id: TMDB, name: 'Show', videos: { results: [] } });
      if (url.includes('/api/clearlogo/')) return json({ clearlogo_url: null });
      return json({});
    });
    vi.stubGlobal('fetch', f);
    const { result } = renderHook(() => useTvDetail(SHOW, undefined));
    await waitFor(() => expect(result.current.show?.logoSettled).toBe(true));
    expect(logoInput(result.current.show)).toBeNull();
    expect(count(f, '/api/clearlogo/')).toBe(1);
  });
});
