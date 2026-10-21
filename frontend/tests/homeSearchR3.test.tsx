/** v227 R3 (Taylor's pick): desktop and tablet Home search at the top of the page. */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const modals = { openMovie: vi.fn(), openTv: vi.fn(), close: vi.fn() };
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => modals }));

import { HomeSearchR3, buildR3, r3Css, R3_BAND } from '@/components/search/HomeSearchR3';
import { HomeSearchPalette } from '@/components/search/HomeSearchPalette';
import { openSearch } from '@/components/search/MinimalSearchHost';

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const LIB = { movies: [{ rating_key: 1, title: 'Dune', year: 2021, tmdb_id: 438631, backdrop_url: '/api/img/plex?url=/art/1' },
  { rating_key: 3, title: 'Dune: Part Two', year: 2024, tmdb_id: 693134 }],
  tv_shows: [{ rating_key: 2, title: 'Dune: Prophecy', year: 2024, tmdb_id: 90228 }], people: [] };
// the TMDb response, mocked: never the real API
const TM = { movies: [{ id: 841, title: 'Dune', release_date: '1984-12-14' }, { id: 5, title: 'Unrelated Thing', release_date: '2000-01-01' }],
  tv_shows: [{ id: 1880, name: "Frank Herbert's Dune", first_air_date: '2000-12-03' }],
  people: [{ id: 137427, name: 'Denis Villeneuve', known_for_department: 'Directing', profile_path: '/x.jpg' }] };
const calls: string[] = [];
const posts: { url: string; body: unknown }[] = [];
let wl: number[] = [];
const fetchMock = vi.fn(async (u: string, init?: RequestInit) => {
  const url = String(u); calls.push(url);
  // the native watchlist and the profile sheet, mocked
  if (url.includes('/api/movies/watchlist/tmdb-ids')) return json({ tmdb_ids: wl });
  if (url.includes('/api/movies/watchlist') && init?.method === 'POST') { posts.push({ url, body: JSON.parse(String(init.body)) }); return json({ ok: true }); }
  if (url.includes('/api/settings/movies')) return json({ profiles: [{ id: 7, name: 'HD 1080p', default: true }] });
  if (url.includes('/api/library/search')) return json(LIB);
  if (url.startsWith('/search?') || url.includes('/search?q=')) return json(TM);
  return json({});
});
beforeEach(() => { calls.length = 0; posts.length = 0; wl = []; window.__LUMA_MINIMAL__ = true; vi.useFakeTimers({ shouldAdvanceTime: true }); vi.stubGlobal('fetch', fetchMock); modals.openMovie.mockReset(); modals.openTv.mockReset(); });
afterEach(() => { delete window.__LUMA_MINIMAL__; cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const type = async (q: string) => {
  fireEvent.change(screen.getByTestId('search-input'), { target: { value: q } });
  await act(async () => { vi.advanceTimersByTime(300); });
  await act(async () => { await Promise.resolve(); });
};

describe('R3 layout (1440 and 1024)', () => {
  it('rest: a Search title and field at the top of the page, the hero a short band, no results', () => {
    render(<HomeSearchR3 />);
    expect(screen.getByRole('heading', { name: 'Search' })).toBeTruthy();
    expect((screen.getByTestId('search-input') as HTMLInputElement).placeholder).toBe('Search');
    expect(screen.queryByTestId('r3-results')).toBeNull();
    expect(screen.getByTestId('r3-cover')).toBeTruthy();       // v228: the band at rest too
    expect(r3Css).toContain('.r3-head{position:fixed;z-index:55;top:104px');
  });
  it('sizes: field 640px at 1440 and 520px at 1024; top card 420/300px; posters 136/112px; band 200px', () => {
    const at = (expr: string, vw: number) => {   // evaluate the clamp(min, Nvw, max) at a viewport width
      const m = /clamp\((\d+)px, ([\d.]+)vw, (\d+)px\)/.exec(expr)!;
      return Math.min(Number(m[3]), Math.max(Number(m[1]), (Number(m[2]) * vw) / 100));
    };
    const v = (name: string) => new RegExp(`${name}:(clamp\\([^)]*\\))`).exec(r3Css)![1]!;
    const field = /\.r3-field\{[^}]*width:(clamp\([^)]*\))/.exec(r3Css)![1]!;
    expect([at(field, 1440), at(field, 1024)]).toEqual([633.6, 520]);
    expect([at(v('--r3-top'), 1440), at(v('--r3-top'), 1024)]).toEqual([417.6, 300]);
    expect([at(v('--r3-po'), 1440), at(v('--r3-po'), 1024)]).toEqual([136, 112]);
    expect([at(v('--r3-pad'), 1440), at(v('--r3-pad'), 1024)]).toEqual([48, 34.816]);
    expect(R3_BAND).toBe(200);
    expect(r3Css).toContain(`.r3-body{position:fixed;z-index:45;top:${R3_BAND}px`);
  });
  it('searching: the hero shrinks to a band; a top-result card beside a mixed grid, then People and Find more', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    expect(screen.getByTestId('r3-search').dataset.searching).toBe('true');
    expect(screen.getByTestId('r3-cover')).toBeTruthy();
    expect(screen.getByTestId('r3-top-title').textContent).toBe('Dune');
    const main = screen.getByTestId('r3-main');
    expect(main.firstElementChild).toBe(screen.getByTestId('r3-top'));
    expect(main.lastElementChild).toBe(screen.getByTestId('r3-grid'));
    const cards = screen.getAllByTestId('r3-card');
    // v228: the rest of your library, then TMDb titles (up to 6)
    expect(cards.map(c => c.textContent)).toEqual(['✓In libraryDune: Part Two2024 · Movie', '✓In libraryDune: Prophecy2024 · TV',
      'Dune1984 · Movie', "Frank Herbert's Dune2000 · TV"]);
    expect(screen.getAllByTestId('r3-person').map(p => p.textContent)).toEqual(['Denis VilleneuveDirecting']);
    expect(screen.queryByText('Play')).toBeNull();    // PM: no new Play buttons
    fireEvent.click(screen.getByTestId('r3-details'));
    expect(modals.openMovie).toHaveBeenCalledWith({ ratingKey: 1, tmdbId: 438631, origin: 'search' });
    fireEvent.keyDown(screen.getByTestId('search-input'), { key: 'Escape' });
    expect(screen.queryByTestId('r3-results')).toBeNull();
  });
});

describe('R3: no key badges, no helper text', () => {
  it('no Esc or / keycaps on R3 or the C2 palette', async () => {
    render(<><HomeSearchR3 /></>);
    await type('dune');
    expect(document.body.textContent).not.toMatch(/\bEsc\b/);
    expect(document.querySelector('.lsp-key, .luma-home-pill-key, kbd')).toBeNull();
    cleanup();
    render(<HomeSearchPalette />);
    act(() => openSearch());
    expect(screen.getByTestId('search-palette').textContent).not.toContain('Esc');
  });
  it('the C2 palette stands down on the kiosk Home; open-search focuses the R3 field', async () => {
    render(<><HomeSearchR3 /><HomeSearchPalette /></>);
    act(() => openSearch());
    expect(screen.queryByTestId('search-palette')).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId('search-input'));
  });
});

describe('R3 Find more is TMDb', () => {
  it('counts and opens the TMDb titles from /search (mocked), word-match filtered, opening the TMDb popup', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    expect(calls.some(u => u.startsWith('/search?q=dune&type=all'))).toBe(true);
    const more = screen.getByTestId('find-more');
    expect(more.textContent).toBe('Find more for “dune”2 on TMDb');
    fireEvent.click(more);
    const tm = screen.getByTestId('r3-tmdb');
    const cards = tm.querySelectorAll('[data-testid="r3-card"]');
    expect([...cards].map(c => c.textContent)).toEqual(['Dune1984 · Movie', "Frank Herbert's Dune2000 · TV"]);
    expect(tm.textContent).not.toContain('Unrelated Thing');
    fireEvent.click(cards[1]!);
    expect(modals.openTv).toHaveBeenCalledWith({ ratingKey: undefined, tmdbId: 1880, origin: 'search' });
  });
  it('nothing owned: the first TMDb title is the top result and leaves Find more', () => {
    const v = buildR3({ owned: [], unowned: [{ id: 841, title: 'Dune', year: 1984, media_type: 'movie' }, { id: 9, title: 'Dune World', year: 2001, media_type: 'tv' }],
      ownedPeople: [], unownedPeople: [], rateLimited: false, failed: false }, 'dune');
    expect([v.top?.id, v.more.map(m => m.id)]).toEqual([841, [9]]);
  });
});

describe('v228 R3 watchlist + on TMDb cards (mocked native watchlist)', () => {
  it('TMDb movie cards get +, library and TV cards none; + opens the profile sheet and adds; then a check', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    const adds = screen.getAllByTestId('r3-wl-add');
    expect(adds.map(b => b.getAttribute('aria-label'))).toEqual(['Add Dune to watchlist']);
    fireEvent.click(adds[0]!);
    const confirm = await screen.findByTestId('watchlist-add-confirm');
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    fireEvent.click(confirm);
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    expect(posts).toEqual([{ url: expect.stringContaining('/api/movies/watchlist'), body: { tmdb_id: 841, title: 'Dune', year: 1984, profile_id: 7 } }]);
    expect(screen.queryByTestId('r3-wl-add')).toBeNull();
    expect(screen.getByTestId('r3-wl-in').getAttribute('aria-label')).toBe('Dune is in your watchlist');
    expect(modals.openMovie).not.toHaveBeenCalled();
  });
  it('after Find more too; a title already on the watchlist shows the check', async () => {
    wl = [841];
    render(<HomeSearchR3 />);
    await type('dune');
    fireEvent.click(screen.getByTestId('find-more'));
    const tm = screen.getByTestId('r3-tmdb');
    expect(tm.querySelectorAll('[data-testid="r3-wl-in"]')).toHaveLength(1);
    expect(tm.querySelectorAll('[data-testid="r3-wl-add"]')).toHaveLength(0);
  });
  it('no + outside the test install (no native watchlist)', async () => {
    delete window.__LUMA_MINIMAL__;
    render(<HomeSearchR3 />);
    await type('dune');
    expect(screen.queryByTestId('r3-wl-add')).toBeNull();
    expect(calls.some(u => u.includes('/api/movies/watchlist'))).toBe(false);
  });
});

import { recentAdded } from '@/pages/MinimalHomePage';
describe('v228 R3 rest: Recently added under the band', () => {
  const card = (k: string) => ({ key: k, type: 'movie', title: `T${k}`, subtitle: '2021', poster: `/p/${k}` });
  it('Plex recently-added rails only (no Trakt, no watchlist), interleaved, 12 at most', () => {
    const data = { plex: true, continue_watching: [], rails: [
      { id: '1', title: 'Movies', type: 'movie', items: ['a1', 'a2'].map(card) },
      { id: 'trakt_movies', title: 'Trending', type: 'movie', items: ['t1'].map(card) },
      { id: 'watchlist', title: 'Watchlist', type: 'movie', items: ['w1'].map(card) },
      { id: '2', title: 'TV', type: 'show', items: ['b1', 'b2', 'b3'].map(card) }] };
    expect(recentAdded(data).map(c => c.key)).toEqual(['a1', 'b1', 'a2', 'b2', 'b3']);
    expect(recentAdded({ ...data, rails: [{ id: '1', title: 'M', type: 'movie', items: Array.from({ length: 30 }, (_, i) => card(String(i))) }] })).toHaveLength(12);
    expect(recentAdded(null)).toEqual([]);
  });
  it('rest shows Recently added posters that open the popup; searching hides it', async () => {
    const open = vi.fn();
    render(<HomeSearchR3 recent={[card('1'), card('2')]} onOpenCard={open} />);
    expect(screen.getByRole('heading', { name: 'Recently added' })).toBeTruthy();
    const posters = screen.getAllByTestId('r3-recent');
    expect(posters.map(p => p.textContent)).toEqual(['✓In libraryT12021', '✓In libraryT22021']);
    fireEvent.click(posters[1]!);
    expect(open).toHaveBeenCalledWith(card('2'));
    await type('dune');
    expect(screen.queryByTestId('r3-rest')).toBeNull();
  });
});
