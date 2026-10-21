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
    expect(r3Css).toContain('.r3-head{position:fixed;z-index:55;top:84px;left:50%;transform:translateX(-50%)');   // v243 A1: inside the bar
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
  it('searching (v246 D3): the hero shrinks to a band; chips over one grid of titles, then people; no top card, no Find more', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    expect(screen.getByTestId('r3-search').dataset.searching).toBe('true');
    expect(screen.getByTestId('r3-cover')).toBeTruthy();
    expect(screen.queryByTestId('r3-top')).toBeNull();
    expect(screen.queryByTestId('find-more')).toBeNull();
    const cards = screen.getAllByTestId('r3-card');
    expect(cards.map(c => c.textContent)).toEqual(['Dune2021 · Movie', 'Dune: Part Two2024 · Movie', 'Dune: Prophecy2024 · TV',
      'Dune1984 · Movie', "Frank Herbert's Dune2000 · TV"]);
    expect(screen.getAllByTestId('r3-person').map(p => p.textContent)).toEqual(['Denis VilleneuvePerson']);
    expect(screen.getByTestId('r3-grid').lastElementChild).toBe(screen.getByTestId('r3-person'));
    expect(screen.queryByText('Play')).toBeNull();
    fireEvent.click(cards[0]!);
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
  it('v246: every TMDb title not already in your library is in the one grid (word-match filtered, no repeats)', async () => {
    tm = { ...MANY, tv_shows: [{ id: 1880, name: "Frank Herbert's Dune", first_air_date: '2000-12-03' }],
      movies: [...MANY.movies, { id: 5, title: 'Unrelated Thing', release_date: '2000-01-01' }] };
    render(<HomeSearchR3 />);
    await type('dune');
    expect(calls.some(u => u.startsWith('/search?q=dune&type=all'))).toBe(true);
    const names = screen.getAllByTestId('r3-card').map(c => c.getAttribute('aria-label'));
    expect(names.length).toBeGreaterThan(6);
    expect(new Set(names).size).toBe(names.length);
    expect(names).not.toContain('Unrelated Thing');
    expect(screen.queryByTestId('find-more')).toBeNull();
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
    const tmS = screen.getByTestId('r3-grid');
    expect([...tmS.querySelectorAll('[data-testid="r3-wl-in"]')].map(e => e.getAttribute('aria-label'))).toEqual(['Dune 8 is in your watchlist']);
    expect(tmS.querySelectorAll('[data-testid="r3-wl-add"]')).toHaveLength(tmS.querySelectorAll('[data-testid="r3-card"]').length - 1 - 3);  // minus the check and 3 library titles
  });
  it.each([404, 500])('v231 (Tester): the id lookup fails (%i): no + anywhere, not even after Find more', async (st) => {
    tm = MANY; wlStatus = st;
    render(<HomeSearchR3 />);
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByTestId('r3-wl-add')).toBeNull();
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

describe('v243 A1: the top bar opens from a bare chevron', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  const bar = () => (screen.getByTestId('a1-bar').dataset.open === 'true' ? screen.getByTestId('a1-bar') : null);
  const head = () => { const h = screen.getByTestId('r3-head'); return { hidden: h.dataset.open !== 'true' || h.hasAttribute('inert') }; };
  it('closed at rest: only the chevron (no circle or background, 48px); search and buttons hidden', () => {
    render(<HomeSearchR3 />);
    expect(bar()).toBeNull();
    expect(head().hidden).toBe(true);
    const chev = screen.getByTestId('a1-chevron');
    expect(chev.getAttribute('aria-expanded')).toBe('false');
    expect(chev.querySelector('svg')!.getAttribute('width')).toBe('48');
    expect(r3Css).toContain('.a1-chev{position:fixed;z-index:56;top:28px;left:50%;transform:translateX(-50%);width:48px;height:48px;padding:0;border:0;background:none;box-shadow:none;');
  });
  it('the chevron opens the glass bar with the search and Watchlist, Library, Explore; the chevron closes it', () => {
    render(<HomeSearchR3 />);
    fireEvent.click(screen.getByTestId('a1-chevron'));
    expect(bar()).toBeTruthy();
    expect(head().hidden).toBe(false);
    expect(screen.getByTestId('a1-chevron').getAttribute('aria-expanded')).toBe('true');
    expect([...screen.getByTestId('a1-nav').querySelectorAll('button')].map(b => b.textContent)).toEqual(['Watchlist', 'Library', 'Explore']);
    expect(r3Css).toContain('.a1-bar{position:fixed;z-index:49;top:0;left:0;right:0;height:152px;background:rgba(17,19,24,.55);backdrop-filter:blur(24px)');
    expect(r3Css).not.toMatch(/a1-in|animation:a1/);   // v245: no separate, later backdrop animation
    fireEvent.click(screen.getByTestId('a1-chevron'));
    expect(bar()).toBeNull();
    expect(head().hidden).toBe(true);
  });
  it('Esc closes it', () => {
    render(<HomeSearchR3 />);
    fireEvent.click(screen.getByTestId('a1-chevron'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(bar()).toBeNull();
  });
  it('a click outside closes it; clicks inside the bar do not', () => {
    render(<><p data-testid="outside">hero</p><HomeSearchR3 /></>);
    fireEvent.click(screen.getByTestId('a1-chevron'));
    fireEvent.pointerDown(screen.getByTestId('search-input'));
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Library' }));
    fireEvent.pointerDown(screen.getByTestId('a1-bar'));
    expect(bar()).toBeTruthy();
    fireEvent.pointerDown(screen.getByTestId('outside'));
    expect(bar()).toBeNull();
  });
  it('on the test install the buttons do nothing (no such pages)', () => {
    render(<HomeSearchR3 />);
    fireEvent.click(screen.getByTestId('a1-chevron'));
    const before = window.location.pathname;
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist' }));
    expect(window.location.pathname).toBe(before);
    expect(bar()).toBeTruthy();
  });
  it('outside the test install they go to /watchlist, /browse and /explore', () => {
    delete window.__LUMA_MINIMAL__;
    render(<HomeSearchR3 />);
    const seen: string[] = [];
    for (const name of ['Watchlist', 'Library', 'Explore']) {
      fireEvent.click(screen.getByTestId('a1-chevron'));
      fireEvent.click(screen.getByRole('button', { name }));
      seen.push(window.location.pathname);
    }
    expect(seen).toEqual(['/watchlist', '/browse', '/explore']);
    window.history.pushState({}, '', '/');
  });
});

describe('v244: uniform 2:3 tiles, no overlap, 2-line titles, a placeholder for no poster', () => {
  it('each tile is exactly one track wide; the poster fills it at 2:3 (the button no longer sizes to its image)', () => {
    expect(r3Css).toContain('.r3-grid{flex:1;min-width:0;display:grid;grid-template-columns:repeat(auto-fill, var(--r3-po));gap:20px 16px;align-items:start}');
    expect(r3Css).toContain('.r3-cardw{position:relative;min-width:0;width:var(--r3-po);overflow:hidden}');
    expect(r3Css).toContain('.r3-card{display:flex;flex-direction:column;align-items:stretch;width:100%;box-sizing:border-box;');
    expect(r3Css).toContain('.r3-po{position:relative;display:block;width:100%;aspect-ratio:2/3;');
    expect(r3Css).toContain('.r3-po img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;');
    expect(r3Css).toMatch(/\.r3-ct\{[^}]*-webkit-line-clamp:2;[^}]*overflow:hidden/);
    expect(r3Css).toMatch(/\.r3-wl\{position:absolute;top:8px;right:8px;/);
  });
  it('no poster (or a broken one): a styled placeholder with the title; the + stays in the tile', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    const cards = screen.getAllByTestId('r3-card');
    const ph = cards[3]!.querySelector('[data-testid="r3-placeholder"]') as HTMLElement;
    expect(ph.dataset.title).toBe('Dune');
    expect(cards[3]!.querySelector('img')).toBeNull();
    expect(ph.parentElement!.className).toBe('r3-po');
    const w = cards[3]!.parentElement!;
    expect(w.className).toBe('r3-cardw');
    expect(w.querySelector('[data-testid="r3-wl-add"]')!.parentElement).toBe(w);
  });
  it('a poster that fails to load turns into the placeholder', async () => {
    tm = { ...TM, movies: [{ id: 841, title: 'Dune', release_date: '1984-12-14', poster_path: '/broken.jpg' }] };
    render(<HomeSearchR3 />);
    await type('dune');
    const card = screen.getAllByTestId('r3-card').find(c => c.textContent!.startsWith('Dune1984'))!;
    fireEvent.error(card.querySelector('img')!);
    expect(card.querySelector('img')).toBeNull();
    expect((card.querySelector('[data-testid="r3-placeholder"]') as HTMLElement).dataset.title).toBe('Dune');
  });
});

describe('v245: one seamless reveal', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  it('the glass bar and the controls share one transition (same properties, duration and easing), no delay on open', () => {
    expect(r3Css).toContain('[data-r3-search]{--a1-dur:240ms;--a1-ease:cubic-bezier(.2,0,0,1)}');
    expect(r3Css).toContain('.a1-bar,.r3-head{transition:transform var(--a1-dur) var(--a1-ease),opacity var(--a1-dur) var(--a1-ease),visibility 0s linear var(--a1-dur)}');
    expect(r3Css).toContain('.a1-bar[data-open="true"],.r3-head[data-open="true"]{opacity:1;visibility:visible;transition-delay:0s}');
    expect((r3Css.match(/--a1-dur\)/g) || []).length).toBe(3);   // the one shared rule: transform, opacity, visibility delay
    expect(r3Css).not.toMatch(/\.a1-bar\{[^}]*(transition|animation)/);
  });
  it('both are always mounted and flip state in the same render (open and close)', () => {
    render(<HomeSearchR3 />);
    const b = screen.getByTestId('a1-bar'); const h = screen.getByTestId('r3-head');
    expect([b.dataset.open, h.dataset.open]).toEqual(['false', 'false']);
    fireEvent.click(screen.getByTestId('a1-chevron'));
    expect([b.dataset.open, h.dataset.open]).toEqual(['true', 'true']);
    expect(screen.getByTestId('a1-bar')).toBe(b);
    fireEvent.click(screen.getByTestId('a1-chevron'));
    expect([b.dataset.open, h.dataset.open]).toEqual(['false', 'false']);
    expect(h.hasAttribute('inert')).toBe(true);
  });
  it('a reused card with a new poster URL shows the new image (the broken state belongs to the old URL)', async () => {
    tm = { ...TM, movies: [{ id: 841, title: 'Dune', release_date: '1984-12-14', poster_path: '/broken.jpg' }] };
    render(<HomeSearchR3 />);
    await type('dune');
    const find = () => screen.getAllByTestId('r3-card').find(c => c.textContent!.startsWith('Dune1984'))!;
    fireEvent.error(find().querySelector('img')!);
    expect(find().querySelector('img')).toBeNull();
    tm = { ...TM, movies: [{ id: 841, title: 'Dune', release_date: '1984-12-14', poster_path: '/fixed.jpg' }] };
    await type('dune ');
    const img = find().querySelector('img');
    expect(img && img.getAttribute('src')).toContain('fixed.jpg');
  });
});

describe('v246 D3: filter chips over a 7-column grid', () => {
  const labels = () => screen.getAllByTestId('r3-card').map(c => c.getAttribute('aria-label'));
  const people = () => screen.queryAllByTestId('r3-person').map(p => p.textContent);
  it('chips with counts; All is on first', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    const chips = [...screen.getByTestId('d3-chips').querySelectorAll('button')];
    expect(chips.map(c => c.textContent)).toEqual(['All 6', 'Movies 3', 'TV 2', 'People 1', 'In library 3']);
    expect(chips.map(c => c.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false', 'false', 'false']);
  });
  it('each chip filters on the client (no new requests)', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    const n = calls.length;
    fireEvent.click(screen.getByTestId('d3-chip-movie'));
    expect(screen.getByTestId('d3-chip-movie').getAttribute('aria-pressed')).toBe('true');
    expect(labels()).toEqual(['Dune', 'Dune: Part Two', 'Dune']); expect(people()).toEqual([]);
    fireEvent.click(screen.getByTestId('d3-chip-tv'));
    expect(labels()).toEqual(['Dune: Prophecy', "Frank Herbert's Dune"]); expect(people()).toEqual([]);
    fireEvent.click(screen.getByTestId('d3-chip-people'));
    expect(screen.queryAllByTestId('r3-card')).toHaveLength(0); expect(people()).toEqual(['Denis VilleneuvePerson']);
    fireEvent.click(screen.getByTestId('d3-chip-owned'));
    expect(labels()).toEqual(['Dune', 'Dune: Part Two', 'Dune: Prophecy']); expect(people()).toEqual([]);
    fireEvent.click(screen.getByTestId('d3-chip-all'));
    expect(labels()).toHaveLength(5); expect(people()).toHaveLength(1);
    expect(calls.length).toBe(n);
  });
  it('a new search goes back to All', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    fireEvent.click(screen.getByTestId('d3-chip-tv'));
    await type('dune part');
    expect(screen.getByTestId('d3-chip-all').getAttribute('aria-pressed')).toBe('true');
  });
  it('no In library badge or overlay on any poster', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    expect(screen.queryByTestId('r3-in-library')).toBeNull();
    expect(document.querySelector('.r3-badge')).toBeNull();
    expect(screen.getByTestId('r3-grid').textContent).not.toContain('In library');
  });
  it('v247: chips and grid in one centered max-width container; auto-fill columns; D3 chip styles', () => {
    expect(r3Css).toContain('.d3{max-width:1600px;margin:0 auto}');
    expect(r3Css).toContain('.d3-grid{display:grid;grid-template-columns:repeat(auto-fill, minmax(min(160px, 100%), 1fr));gap:28px clamp(12px, 2.1vw, 30px);');
    expect(r3Css).not.toContain('repeat(7');
    expect(r3Css).toContain('.d3-grid .r3-cardw{width:100%}');
    expect(r3Css).toMatch(/\.d3-chip\{height:36px;padding:0 16px;border-radius:8px;border:1px solid #44474e;/);
    expect(r3Css).toContain('.d3-chip[aria-pressed="true"]{background:#3a4458;border-color:#3a4458;color:#d8e2ff}');
  });
});
