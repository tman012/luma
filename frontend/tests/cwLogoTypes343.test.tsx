/** cw-logo: every Continue Watching item type ends with its clearlogo when one exists. A CW card names the TMDb id of
 *  what it opens (tmdb_hint: the movie, or an episode's/season's show), so the open starts the logo at the tap like a
 *  search row; without a hint the sheet takes the id from Plex and starts the logo then, alongside the TMDb details. */
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTvDetail } from '@/hooks/useTvDetail';
import { useMovieDetail } from '@/hooks/useMovieDetail';
import { openDetail } from '@/pages/MinimalHomePage';
import { cardIds } from '@/components/home/Rails';
import { _resetModalPrefetch } from '@/services/modalPrefetch';
import { _resetModalColors } from '@/services/modalColors';
import { logoInput } from '@/hooks/useTitleReveal';
import type { Card } from '@/components/home/homeApi';

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
const LOGO = 'https://image.tmdb.org/t/p/original/logo.png';
const card = (c: Partial<Card>): Card => ({ key: '1', type: 'movie', title: 'T', subtitle: '', poster: null, ...c } as Card);
const opened = (c: Card) => {
  const openMovie = vi.fn(); const openTv = vi.fn();
  openDetail({ openMovie, openTv }, c);
  return (openMovie.mock.calls[0] ?? openTv.mock.calls[0])[0] as { ratingKey?: number; tmdbId?: number };
};

describe('CW cards open what they show, with the TMDb id the logo needs', () => {
  it('movie: its own key and TMDb id', () => {
    const c = card({ key: '900', type: 'movie', tmdb_hint: 603 });
    expect(opened(c)).toMatchObject({ ratingKey: 900, tmdbId: 603 });
    expect(cardIds(c)).toEqual({ kind: 'movie', ratingKey: 900, tmdbId: 603 });
  });
  it('episode: the show key and the show TMDb id, never the episode key', () => {
    const c = card({ key: '4591', type: 'episode', show_key: '4501', season: 1, episode: 2, tmdb_hint: 6191 });
    expect(opened(c)).toMatchObject({ ratingKey: 4501, tmdbId: 6191 });
    expect(cardIds(c)).toEqual({ kind: 'tv', ratingKey: 4501, tmdbId: 6191 });
  });
  it('season: the show key and the show TMDb id', () => {
    const c = card({ key: '4550', type: 'season', show_key: '4501', tmdb_hint: 6191 });
    expect(opened(c)).toMatchObject({ ratingKey: 4501, tmdbId: 6191 });
  });
  it('no hint (Plex names no TMDb id on the card): the Plex key alone, as before', () => {
    const c = card({ key: '4591', type: 'episode', show_key: '4501' });
    expect(opened(c)).toEqual({ ratingKey: 4501, origin: 'recently_added_tv' });
    expect(cardIds(c)).toEqual({ kind: 'tv', ratingKey: 4501 });
  });
});

describe('each CW open ends with the logo', () => {
  beforeEach(() => { _resetModalPrefetch(); _resetModalColors(); });
  afterEach(() => { vi.unstubAllGlobals(); });
  let releaseTmdb: () => void = () => {};
  const stub = (plexTmdb: number | null, opts: { holdTmdb?: boolean } = {}) => {
    const tmdbHeld = new Promise<void>((r) => { releaseTmdb = r; });
    const f = vi.fn(async (u: string) => {
      const url = String(u);
      if (url.includes('/api/tv/seasons/')) return json({ success: true, tvdb_id: null, seasons: [] });
      if (url.includes('/api/plex/tv/4501')) return json({ title: 'Show', year: 1959, tmdb_id: plexTmdb, cast: [] });
      if (url.includes('/api/plex/movie/900')) return json({ title: 'Film', year: 1999, tmdb_id: 603, cast: [], directors: [] });
      if (url.includes('/api/plex/')) return json({});
      if (url.includes('/api/tv/6191') || url.includes('/api/movie/603')) {
        if (opts.holdTmdb) await tmdbHeld;
        return json({ id: 1, name: 'Show', title: 'Film', backdrop_path: '/b.jpg', videos: { results: [] } });
      }
      if (url.includes('/api/clearlogo/6191?type=tv') || url.includes('/api/clearlogo/603')) return json({ clearlogo_url: LOGO });
      if (url.includes('/api/clearlogo/')) return json({ clearlogo_url: null });
      return json({});
    });
    vi.stubGlobal('fetch', f);
    return f;
  };
  const count = (f: ReturnType<typeof vi.fn>, part: string) => f.mock.calls.filter((c) => String(c[0]).includes(part)).length;

  it('episode / season with the show hint (search-like open): logo', async () => {
    stub(6191);
    const { result } = renderHook(() => useTvDetail(4501, 6191));
    await waitFor(() => expect(logoInput(result.current.show)).toMatch(/logo\.png/));
  });
  it('episode without a hint (Plex guid fallback): the logo lookup starts with the TMDb details, not after them', async () => {
    const f = stub(6191, { holdTmdb: true });
    const { result } = renderHook(() => useTvDetail(4501, undefined));
    await waitFor(() => expect(count(f, '/api/clearlogo/6191?type=tv')).toBe(1));   // TMDb details still out
    releaseTmdb();
    await waitFor(() => expect(logoInput(result.current.show)).toMatch(/logo\.png/));
    expect(count(f, '/api/clearlogo/6191?type=tv')).toBe(1);
  });
  it('movie with its hint: logo', async () => {
    stub(null);
    const { result } = renderHook(() => useMovieDetail(900, 603));
    await waitFor(() => expect(logoInput(result.current.detail)).toMatch(/logo\.png/));
  });
  it('movie without a hint: the logo lookup starts with Plex\'s id, while TMDb is still out', async () => {
    const f = stub(null, { holdTmdb: true });
    const { result } = renderHook(() => useMovieDetail(900, undefined));
    await waitFor(() => expect(count(f, '/api/clearlogo/603')).toBe(1));
    releaseTmdb();
    await waitFor(() => expect(logoInput(result.current.detail)).toMatch(/logo\.png/), { timeout: 3000 });
  });
  it('a show with no TMDb id anywhere settles as text (no lookup by a Plex key)', async () => {
    const f = stub(null);
    const { result } = renderHook(() => useTvDetail(4501, undefined));
    await waitFor(() => expect(result.current.show?.logoSettled).toBe(true));
    expect(logoInput(result.current.show)).toBeNull();
    expect(count(f, '/api/clearlogo/4501')).toBe(0);
  });
});
