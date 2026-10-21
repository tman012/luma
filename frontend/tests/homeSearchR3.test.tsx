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
let wlStatus = 200;
let tm: unknown = TM;
// v231: the TV library (tv.db), mocked; POST /shows is recorded, never sent anywhere
let tvShows: { tmdb_id: number }[] = [];
let tvPaths = [{ id: 'p1', name: 'TV', path: '/media/tv', status: 'ok' }];
const tvPosts: unknown[] = [];
const fetchMock = vi.fn(async (u: string, init?: RequestInit) => {
  const url = String(u); calls.push(url);
  // the native watchlist and the profile sheet, mocked
  if (url.includes('/api/movies/watchlist/tmdb-ids')) return wlStatus === 200 ? json({ tmdb_ids: wl }) : new Response('{}', { status: wlStatus });
  if (url.endsWith('/api/settings/tv/shows') && init?.method === 'POST') { tvPosts.push(JSON.parse(String(init.body))); return json({ ok: true, series_id: 77 }); }
  if (url.endsWith('/api/settings/tv/profiles')) return json({ profiles: [{ id: 3, name: 'HD', default: true }], qualities: [] });
  if (url.endsWith('/api/settings/tv/library')) return json({ shows: tvShows, unmatched: [], scan: {} });
  if (url.endsWith('/api/settings/tv')) return json({ paths: tvPaths, upgrades: { mode: 'off' }, rescan_interval_h: 6 });
  if (url.includes('/api/movies/watchlist') && init?.method === 'POST') { posts.push({ url, body: JSON.parse(String(init.body)) }); return json({ ok: true }); }
  if (url.includes('/api/settings/movies')) return json({ profiles: [{ id: 7, name: 'HD 1080p', default: true }] });
  if (url.includes('/api/library/search')) return json(LIB);
  if (url.startsWith('/search?') || url.includes('/search?q=')) return json(tm);
  return json({});
});
beforeEach(() => { calls.length = 0; posts.length = 0; wl = []; wlStatus = 200; tm = TM; tvShows = []; tvPosts.length = 0; tvPaths = [{ id: 'p1', name: 'TV', path: '/media/tv', status: 'ok' }]; window.__LUMA_MINIMAL__ = true; vi.useFakeTimers({ shouldAdvanceTime: true }); vi.stubGlobal('fetch', fetchMock); modals.openMovie.mockReset(); modals.openTv.mockReset(); });
afterEach(() => { delete window.__LUMA_MINIMAL__; cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const type = async (q: string) => {
  fireEvent.change(screen.getByTestId('search-input'), { target: { value: q } });
  await act(async () => { vi.advanceTimersByTime(300); });
  await act(async () => { await Promise.resolve(); });
};

describe('R3 layout (1440 and 1024)', () => {
  it('rest (v235): only the field, centered in the top bar; no heading, no band, no results', () => {
    render(<HomeSearchR3 />);
    expect(screen.queryByRole('heading', { name: 'Search' })).toBeNull();
    expect((screen.getByTestId('search-input') as HTMLInputElement).placeholder).toBe('Search movies and shows');   // v241 (H2)
    expect(screen.queryByTestId('r3-results')).toBeNull();
    expect(screen.queryByTestId('r3-cover')).toBeNull();      // the full Home hero shows at rest
    expect(screen.queryByTestId('r3-rest')).toBeNull();
    expect(r3Css).toContain('.r3-head{position:fixed;z-index:55;top:28px;left:50%;transform:translateX(-50%)');
  });
  it('typing shows the band and the results; clearing goes back to rest', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    expect(screen.getByTestId('r3-cover')).toBeTruthy();
    expect(screen.getByTestId('r3-results')).toBeTruthy();
    await type('');
    expect(screen.queryByTestId('r3-cover')).toBeNull();
    expect(screen.queryByTestId('r3-results')).toBeNull();
  });
  it('sizes: field 480x48 (H2); top card 420/300px; posters 136/112px; band 200px', () => {
    const at = (expr: string, vw: number) => {   // evaluate the clamp(min, Nvw, max) at a viewport width
      const m = /clamp\((\d+)px, ([\d.]+)vw, (\d+)px\)/.exec(expr)!;
      return Math.min(Number(m[3]), Math.max(Number(m[1]), (Number(m[2]) * vw) / 100));
    };
    const v = (name: string) => new RegExp(`${name}:(clamp\\([^)]*\\))`).exec(r3Css)![1]!;
    // v241 (H2): a fixed 480x48 pill, radius 24, rgba(51,53,58,.85), no border
    expect(r3Css).toContain('.r3-field{display:flex;align-items:center;gap:12px;width:480px;height:48px;box-sizing:border-box;padding:0 20px;border-radius:24px;');
    expect(r3Css).toContain('background:rgba(51,53,58,.85);border:0;color:#c4c6d0');
    expect(r3Css).toContain('.r3-input::placeholder{color:#c4c6d0;opacity:1}');
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

// v231: enough TMDb titles to fill the grid, so On TMDb has something new to show
const MANY = { movies: Array.from({ length: 8 }, (_, i) => ({ id: 900 + i, title: `Dune ${i + 1}`, release_date: `${1990 + i}-01-01` })), tv_shows: [], people: [] };

describe('R3 Find more is TMDb', () => {
  it('only the titles not already in the grid: counts and opens them from /search (mocked), word-match filtered', async () => {
    tm = { ...MANY, tv_shows: [{ id: 1880, name: "Frank Herbert's Dune", first_air_date: '2000-12-03' }],
      movies: [...MANY.movies, { id: 5, title: 'Unrelated Thing', release_date: '2000-01-01' }] };
    render(<HomeSearchR3 />);
    await type('dune');
    expect(calls.some(u => u.startsWith('/search?q=dune&type=all'))).toBe(true);
    const grid = screen.getByTestId('r3-grid');
    const inGrid = [...grid.querySelectorAll('[data-testid="r3-card"]')].map(c => c.getAttribute('aria-label'));
    expect(inGrid).toHaveLength(6);
    const more = screen.getByTestId('find-more');
    fireEvent.click(more);
    const tmS = screen.getByTestId('r3-tmdb');
    const cards = [...tmS.querySelectorAll('[data-testid="r3-card"]')];
    const names = cards.map(c => c.getAttribute('aria-label'));
    expect(names.length).toBeGreaterThan(0);
    expect(names.filter(n => inGrid.includes(n))).toEqual([]);        // nothing repeats the grid
    expect(more.textContent).toBe(`Find more for “dune”${names.length} on TMDb`);
    expect(tmS.querySelector('.r3-count')!.textContent).toBe(String(names.length));
    expect(tmS.textContent).not.toContain('Unrelated Thing');
    fireEvent.click(cards[0]!);
    expect(modals.openMovie.mock.calls[0]![0]).toMatchObject({ origin: 'search' });
  });
  it('no Find more when every TMDb title is already shown above', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    expect(screen.getAllByTestId('r3-card').map(c => c.getAttribute('aria-label'))).toContain("Frank Herbert's Dune");
    expect(screen.queryByTestId('find-more')).toBeNull();
    expect(screen.queryByTestId('r3-tmdb')).toBeNull();
  });
  it('buildR3: nothing owned, the first TMDb title is the top result; more holds only what the grid left out', () => {
    const rows = Array.from({ length: 9 }, (_, i) => ({ id: 10 + i, title: `Dune ${i}`, year: 2000 + i, media_type: i % 2 ? 'tv' : 'movie' }));
    const base = { owned: [], ownedPeople: [], unownedPeople: [], rateLimited: false, failed: false };
    const v = buildR3({ ...base, unowned: rows } as never, 'dune');
    expect([v.top?.id, v.grid.map(m => m.id), v.more.map(m => m.id)]).toEqual([10, [11, 12, 13, 14, 15, 16], [17, 18]]);
    const few = buildR3({ ...base, unowned: rows.slice(0, 2) } as never, 'dune');
    expect([few.top?.id, few.grid.map(m => m.id), few.more]).toEqual([10, [11], []]);
  });
});

describe('v228 R3 watchlist + on TMDb cards (mocked native watchlist)', () => {
  it('TMDb movie cards get the watchlist +, library cards none; + opens the profile sheet and adds; then a check', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    const adds = screen.getAllByTestId('r3-wl-add');
    expect(adds.map(b => b.getAttribute('aria-label'))).toEqual(['Add Dune to watchlist', "Add Frank Herbert's Dune to your TV library"]);
    fireEvent.click(adds[0]!);
    const confirm = await screen.findByTestId('watchlist-add-confirm');
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    fireEvent.click(confirm);
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    expect(posts).toEqual([{ url: expect.stringContaining('/api/movies/watchlist'), body: { tmdb_id: 841, title: 'Dune', year: 1984, profile_id: 7 } }]);
    expect(screen.getAllByTestId('r3-wl-add').map(b => b.getAttribute('aria-label'))).toEqual(["Add Frank Herbert's Dune to your TV library"]);
    expect(screen.getByTestId('r3-wl-in').getAttribute('aria-label')).toBe('Dune is in your watchlist');
    expect(modals.openMovie).not.toHaveBeenCalled();
  });
  it('after Find more too; a title already on the watchlist shows the check', async () => {
    tm = MANY; wl = [907];
    render(<HomeSearchR3 />);
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(screen.getByTestId('find-more'));
    const tmS = screen.getByTestId('r3-tmdb');
    expect([...tmS.querySelectorAll('[data-testid="r3-wl-in"]')].map(e => e.getAttribute('aria-label'))).toEqual(['Dune 8 is in your watchlist']);
    expect(tmS.querySelectorAll('[data-testid="r3-wl-add"]')).toHaveLength(tmS.querySelectorAll('[data-testid="r3-card"]').length - 1);
  });
  it.each([404, 500])('v231 (Tester): the id lookup fails (%i): no + anywhere, not even after Find more', async (st) => {
    tm = MANY; wlStatus = st;
    render(<HomeSearchR3 />);
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByTestId('r3-wl-add')).toBeNull();
    fireEvent.click(screen.getByTestId('find-more'));
    expect(screen.queryByTestId('r3-wl-add')).toBeNull();
    expect(screen.queryByTestId('r3-wl-in')).toBeNull();
  });
  it('no + outside the test install (no native watchlist)', async () => {
    tvShows = [];
    delete window.__LUMA_MINIMAL__;
    render(<HomeSearchR3 />);
    await type('dune');
    expect(screen.queryByTestId('r3-wl-add')).toBeNull();
    expect(calls.some(u => u.includes('/api/movies/watchlist') || u.includes('/api/settings/tv'))).toBe(false);
  });
});

describe('v231 R3 TV + adds the series as Add series does (mocked tv.db)', () => {
  const tvAdd = () => screen.getAllByTestId('r3-wl-add').find(b => b.getAttribute('aria-label')!.includes('TV library'));
  it('one TV path: + adds at once with Add series defaults (Future episodes, default profile), no picker; then a check', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(tvAdd()!);
    expect(screen.queryByTestId('r3-tv-add-sheet')).toBeNull();
    expect(screen.queryByTestId('tv-add-picker')).toBeNull();
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    expect(tvPosts).toEqual([{ tmdb_id: 1880, monitor: 'future', profile_id: 3 }]);   // one path: no path_id, as Add series
    expect(screen.queryByTestId('r3-tv-add-sheet')).toBeNull();
    expect(tvAdd()).toBeUndefined();
    expect(screen.getAllByTestId('r3-wl-in').map(e => e.getAttribute('aria-label'))).toContain("Frank Herbert's Dune is in your TV library");
    expect(modals.openTv).not.toHaveBeenCalled();
    expect(calls.filter(u => /\/api\/(tv|sonarr|settings\/tv\/(search|downloads))/.test(u))).toEqual([]);   // nothing beyond Add series
  });
  it('two TV paths: the Add series picker opens; a path pick is required and sent', async () => {
    tvPaths = [{ id: 'p1', name: 'TV', path: '/media/tv', status: 'ok' }, { id: 'p2', name: 'Anime', path: '/media/anime', status: 'ok' }];
    render(<HomeSearchR3 />);
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(tvAdd()!);
    expect(screen.getByTestId('r3-tv-add-sheet').textContent).toContain("Add Frank Herbert's Dune");
    expect((screen.getByTestId('tv-add-confirm') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getAllByTestId('tv-add-path')[1]!);
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    fireEvent.click(screen.getByTestId('tv-add-confirm'));
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    expect(tvPosts).toEqual([{ tmdb_id: 1880, path_id: 'p2', monitor: 'future', profile_id: 3 }]);
  });
  it('already in the TV library: a check, no +; Cancel adds nothing', async () => {
    tvShows = [{ tmdb_id: 1880 }];
    render(<HomeSearchR3 />);
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    expect(tvAdd()).toBeUndefined();
    expect(screen.getAllByTestId('r3-wl-in').map(e => e.getAttribute('aria-label'))).toEqual(["Frank Herbert's Dune is in your TV library"]);
    expect(tvPosts).toEqual([]);
  });
  it('Cancel closes the picker without adding (two paths)', async () => {
    tvPaths = [{ id: 'p1', name: 'TV', path: '/media/tv', status: 'ok' }, { id: 'p2', name: 'Anime', path: '/media/anime', status: 'ok' }];
    render(<HomeSearchR3 />);
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(tvAdd()!);
    fireEvent.click(screen.getByTestId('tv-add-cancel'));
    expect(screen.queryByTestId('r3-tv-add-sheet')).toBeNull();
    expect(tvPosts).toEqual([]);
  });
  it('no TV paths (or not the owner): no TV +', async () => {
    tvPaths = [];
    render(<HomeSearchR3 />);
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    expect(tvAdd()).toBeUndefined();
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
});
