import { resultIds } from '@/services/searchApi';
/** v227 R3 (Taylor's pick): desktop and tablet Home search at the top of the page. */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const modals = { openMovie: vi.fn(), openTv: vi.fn(), close: vi.fn() };
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => modals }));

import { HomeSearchR3, buildR3, modalTabStops, r3Css, R3_MODAL_OPEN, R3_MODAL_REST, R3_MODAL_W } from '@/components/search/HomeSearchR3';
import { HomeSearchPalette } from '@/components/search/HomeSearchPalette';
import { openSearch } from '@/components/search/MinimalSearchHost';
import { _resetSheetPriority } from '@/loading/sheetPriority';
// v341k: a sheet open started by an earlier test holds page images until its reveal; each test starts with none.
beforeEach(() => { _resetSheetPriority(); });

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
  it('rest (v235, v358): the field is in the closed search modal; no heading, no band, no results', () => {
    render(<HomeSearchR3 />);
    expect(screen.queryByRole('heading', { name: 'Search' })).toBeNull();
    expect((screen.getByTestId('search-input') as HTMLInputElement).placeholder).toBe('Search movies and shows');   // v241 (H2)
    expect(screen.queryByTestId('r3-results')).toBeNull();
    expect(screen.queryByTestId('r3-cover')).toBeNull();      // the full Home hero shows at rest
    expect(screen.queryByTestId('r3-rest')).toBeNull();
    expect(screen.getByTestId('r3-modal').dataset.open).toBe('false');
    expect(screen.getByTestId('search-modal').contains(screen.getByTestId('search-input'))).toBe(true);
  });
  it('typing shows the results inside the modal (no hero band); clearing goes back to rest', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    expect(screen.queryByTestId('r3-cover')).toBeNull();      // v358: the modal's backdrop, not a band over the hero
    expect(screen.getByTestId('search-modal').contains(screen.getByTestId('r3-results'))).toBe(true);
    expect(screen.getByTestId('search-modal').dataset.searching).toBe('true');
    await type('');
    expect(screen.queryByTestId('r3-results')).toBeNull();
    expect(screen.getByTestId('search-modal').dataset.searching).toBe('false');
  });
  it('sizes: field 48px tall (H2), as wide as the modal; top card 420/300px; posters 136/112px; modal 1040px wide', () => {
    const at = (expr: string, vw: number) => {   // evaluate the clamp(min, Nvw, max) at a viewport width
      const m = /clamp\((\d+)px, ([\d.]+)vw, (\d+)px\)/.exec(expr)!;
      return Math.min(Number(m[3]), Math.max(Number(m[1]), (Number(m[2]) * vw) / 100));
    };
    const v = (name: string) => new RegExp(`${name}:(clamp\\([^)]*\\))`).exec(r3Css)![1]!;
    // v241 (H2): a 48px pill, radius 24, rgba(51,53,58,.85), no border; v358: it fills the modal's field row
    expect(r3Css).toContain('.r3-field{display:flex;align-items:center;gap:12px;width:480px;height:48px;box-sizing:border-box;padding:0 20px;border-radius:24px;');
    expect(r3Css).toContain('.r3-panel .r3-field{flex:1;width:auto;min-width:0}');
    expect(r3Css).toContain('background:rgba(51,53,58,.85);border:0;color:#c4c6d0');
    expect(r3Css).toContain('.r3-input::placeholder{color:#c4c6d0;opacity:1}');
    expect([at(v('--r3-top'), 1440), at(v('--r3-top'), 1024)]).toEqual([417.6, 300]);
    expect([at(v('--r3-po'), 1440), at(v('--r3-po'), 1024)]).toEqual([136, 112]);
    expect([at(v('--r3-pad'), 1440), at(v('--r3-pad'), 1024)]).toEqual([48, 34.816]);
    expect([R3_MODAL_W, R3_MODAL_REST, R3_MODAL_OPEN]).toEqual([1040, 88, 760]);
    expect(r3Css).toContain('.r3-body{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;');
  });
  it('searching (v246 D3): chips over one grid of titles, then people; no top card, no Find more', async () => {
    render(<HomeSearchR3 />);
    await type('dune');
    expect(screen.getByTestId('r3-search').dataset.searching).toBe('true');
    expect(screen.getByTestId('r3-modal').dataset.open).toBe('true');
    expect(screen.queryByTestId('r3-top')).toBeNull();
    expect(screen.queryByTestId('find-more')).toBeNull();
    const cards = screen.getAllByTestId('r3-card');
    expect(cards.map(c => c.textContent)).toEqual(['Dune2021 · Movie', 'Dune: Part Two2024 · Movie', 'Dune: Prophecy2024 · TV',
      'Dune1984 · Movie', "Frank Herbert's Dune2000 · TV"]);
    expect(screen.getAllByTestId('r3-person').map(p => p.textContent)).toEqual(['Denis VilleneuvePerson']);
    expect(screen.getByTestId('r3-grid').lastElementChild).toBe(screen.getByTestId('r3-person'));
    expect(screen.queryByText('Play')).toBeNull();
    fireEvent.click(cards[0]!);
    // v324: Home's openWhenReady path (pressed card, then the popup).
    expect(cards[0]!.getAttribute('data-pressed')).toBe('true');
    await waitFor(() => expect(modals.openMovie).toHaveBeenCalledWith({ ratingKey: 1, tmdbId: 438631, origin: 'search' }));
    // F3: an unowned row carries no Plex key.
    expect(resultIds({ media_type: 'movie', id: 438631, title: 'Dune' } as never)).toEqual(expect.objectContaining({ ratingKey: undefined, tmdbId: 438631 }));
    fireEvent.keyDown(screen.getByTestId('search-input'), { key: 'Escape' });
    expect(screen.queryByTestId('r3-results')).toBeNull();
    expect(screen.getByTestId('r3-modal').dataset.open).toBe('false');   // v358: one Escape closes the modal
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
  it('the C2 palette stands down on the kiosk Home; open-search opens the R3 modal and focuses its field', async () => {
    render(<><HomeSearchR3 /><HomeSearchPalette /></>);
    act(() => openSearch());
    expect(screen.queryByTestId('search-palette')).toBeNull();
    expect(screen.getByTestId('r3-modal').dataset.open).toBe('true');
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

describe('v358 nav bar on Home: Search opens the search modal, nothing drops down', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.pushState({}, '', '/'); document.documentElement.style.overflow = ''; document.body.style.overflow = ''; });
  const modal = () => screen.getByTestId('r3-modal');
  const isOpen = () => modal().dataset.open === 'true';
  const searchBtn = () => screen.getByRole('button', { name: 'Search' });
  const openSearch = () => fireEvent.click(searchBtn());
  const tab = (shift = false) => fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Tab', shiftKey: shift });

  it('at rest the bar is visible and the modal is closed, inert and out of the accessibility tree', () => {
    render(<HomeSearchR3 />);
    expect(screen.getByTestId('desktop-dock')).toBeTruthy();
    expect(isOpen()).toBe(false);
    expect(modal().hasAttribute('inert')).toBe(true);
    expect(modal().getAttribute('aria-hidden')).toBe('true');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByTestId('a1-chevron')).toBeNull();
    expect(screen.queryByTestId('a1-bar')).toBeNull();          // the bar that dropped under the dock is gone
    expect(searchBtn().hasAttribute('aria-expanded')).toBe(false);
    expect(searchBtn().getAttribute('aria-haspopup')).toBe('dialog');
    expect(r3Css).not.toMatch(/a1-bar|r3-cover/);
    expect(r3Css).toContain('.r3-modal[data-open="false"]{opacity:0;visibility:hidden;pointer-events:none}');
  });

  it('Search opens a centered dialog over the page (role=dialog, aria-modal, named Search) and focuses the field', () => {
    render(<HomeSearchR3 />);
    openSearch();
    expect(isOpen()).toBe(true);
    expect(modal().hasAttribute('inert')).toBe(false);
    expect(modal().hasAttribute('aria-hidden')).toBe(false);
    const dialog = screen.getByRole('dialog', { name: 'Search' });
    expect(dialog).toBe(screen.getByTestId('search-modal'));
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(dialog.contains(screen.getByTestId('search-input'))).toBe(true);
    expect(document.activeElement).toBe(screen.getByTestId('search-input'));
    expect(searchBtn().dataset.current).toBe('true');          // the pill is on Search while its modal is open
    // centered over the whole page, above the bar (z 56) and under the person view and the sheets
    expect(r3Css).toContain('.r3-modal{position:fixed;inset:0;z-index:58;display:flex;align-items:center;justify-content:center;padding:48px;');
    expect(r3Css).toContain('.r3-backdrop{position:absolute;inset:0;background:rgba(21,16,42,.72);');
    expect(r3Css).toContain(`.r3-panel{position:relative;width:min(${R3_MODAL_W}px, 100%);height:${R3_MODAL_REST}px;max-height:100%;`);
    expect(r3Css).toContain(`.r3-panel[data-searching="true"]{height:min(${R3_MODAL_OPEN}px, 100%)}`);
    expect(r3Css).toContain('@media (prefers-reduced-motion: reduce){[data-r3-search]{--a1-dur:0ms}}');
    openSearch();                                               // it does not toggle closed
    expect(isOpen()).toBe(true);
    expect(document.activeElement).toBe(screen.getByTestId('search-input'));
  });

  it('Escape closes it and focus returns to the Search button', () => {
    render(<HomeSearchR3 />);
    openSearch();
    expect(document.activeElement).toBe(screen.getByTestId('search-input'));
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(isOpen()).toBe(false);
    expect(document.activeElement).toBe(searchBtn());
    expect(searchBtn().dataset.current).toBeUndefined();
  });

  it('Escape with a query typed closes it in one press, clears the query, and returns focus', async () => {
    render(<HomeSearchR3 />);
    openSearch();
    await type('dune');
    expect(screen.getByTestId('r3-results')).toBeTruthy();
    fireEvent.keyDown(screen.getByTestId('search-input'), { key: 'Escape' });
    expect(isOpen()).toBe(false);
    expect(screen.queryByTestId('r3-results')).toBeNull();
    expect((screen.getByTestId('search-input') as HTMLInputElement).value).toBe('');
    expect(document.activeElement).toBe(searchBtn());
  });

  it('a backdrop click closes it and returns focus; clicks inside the dialog do not close it', () => {
    render(<HomeSearchR3 />);
    openSearch();
    const click = (el: Element) => { fireEvent.mouseDown(el); fireEvent.mouseUp(el); fireEvent.click(el); };
    click(screen.getByTestId('search-input'));
    click(screen.getByTestId('search-modal'));
    click(screen.getByTestId('r3-head'));
    expect(isOpen()).toBe(true);
    // a drag that starts in the field and ends on the backdrop is not a backdrop click
    fireEvent.mouseDown(screen.getByTestId('search-input'));
    fireEvent.click(screen.getByTestId('search-backdrop'));
    expect(isOpen()).toBe(true);
    click(screen.getByTestId('search-backdrop'));
    expect(isOpen()).toBe(false);
    expect(document.activeElement).toBe(searchBtn());
  });

  it('the Close button closes it and returns focus', () => {
    render(<HomeSearchR3 />);
    openSearch();
    const close = screen.getByRole('button', { name: 'Close' });
    expect(close.textContent).toBe('');
    fireEvent.click(close);
    expect(isOpen()).toBe(false);
    expect(document.activeElement).toBe(searchBtn());
  });

  it('focus trap: Tab and Shift+Tab cycle inside the dialog and never reach the page behind it', async () => {
    render(<><button data-testid="behind">behind</button><HomeSearchR3 /></>);
    openSearch();
    const input = screen.getByTestId('search-input');
    const close = screen.getByTestId('search-close');
    expect(modalTabStops(screen.getByTestId('search-modal'))).toEqual([input, close]);
    close.focus();
    tab();
    expect(document.activeElement).toBe(input);                 // last wraps to first
    tab(true);
    expect(document.activeElement).toBe(close);                 // first wraps to last
    screen.getByTestId('behind').focus();                       // focus that escaped comes back on the next Tab
    tab();
    expect(document.activeElement).toBe(input);
    screen.getByTestId('behind').focus();
    tab(true);
    expect(document.activeElement).toBe(close);
    await type('dune');                                         // with results: field, close, chips, cards, add buttons, people
    const stops = modalTabStops(screen.getByTestId('search-modal'));
    expect(stops[0]).toBe(input);
    expect(stops.length).toBeGreaterThan(8);
    expect(stops.every(el => screen.getByTestId('search-modal').contains(el))).toBe(true);
    stops[stops.length - 1]!.focus();
    tab();
    expect(document.activeElement).toBe(input);
    input.focus();
    tab(true);
    expect(document.activeElement).toBe(stops[stops.length - 1]);
    expect(screen.getByTestId('search-modal').contains(screen.getByTestId('behind'))).toBe(false);
  });

  it('body scroll is locked while the modal is open and restored when it closes', () => {
    document.documentElement.style.overflow = 'auto';           // a page that scrolls (useBodyScroll)
    document.body.style.overflow = 'auto';
    render(<HomeSearchR3 />);
    expect([document.documentElement.style.overflow, document.body.style.overflow]).toEqual(['auto', 'auto']);
    openSearch();
    expect([document.documentElement.style.overflow, document.body.style.overflow]).toEqual(['hidden', 'hidden']);
    document.documentElement.style.overflow = 'auto';           // useBodyScroll re-asserts on visibilitychange; the lock answers
    fireEvent(document, new Event('visibilitychange'));
    expect(document.documentElement.style.overflow).toBe('hidden');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect([document.documentElement.style.overflow, document.body.style.overflow]).toEqual(['auto', 'auto']);
  });

  it('v326: Esc and Tab with a sheet open over the search are the sheet\'s; the search stays', () => {
    render(<><div data-modal="MovieDetailModal"><button data-testid="in-sheet">x</button></div><HomeSearchR3 /></>);
    openSearch();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(isOpen()).toBe(true);
    screen.getByTestId('in-sheet').focus();
    tab();
    expect(document.activeElement).toBe(screen.getByTestId('in-sheet'));
  });

  it('search-to-sheet is unchanged: a result opens its sheet over the modal, which stays open with its results', async () => {
    render(<HomeSearchR3 />);
    openSearch();
    await type('dune');
    const cards = screen.getAllByTestId('r3-card');
    fireEvent.click(cards[0]!);
    await waitFor(() => expect(modals.openMovie).toHaveBeenCalledWith({ ratingKey: 1, tmdbId: 438631, origin: 'search' }));
    fireEvent.click(cards[2]!);
    await waitFor(() => expect(modals.openTv).toHaveBeenCalledWith({ ratingKey: 2, tmdbId: 90228, origin: 'search' }));
    expect(isOpen()).toBe(true);
    expect(screen.getAllByTestId('r3-card')).toHaveLength(cards.length);
    // Enter in the field opens the first result, as before
    modals.openMovie.mockReset();
    fireEvent.keyDown(screen.getByTestId('search-input'), { key: 'Enter' });
    await waitFor(() => expect(modals.openMovie).toHaveBeenCalledWith({ ratingKey: 1, tmdbId: 438631, origin: 'search' }));
  });

  it('a person opens the person view (search-query) and the modal closes under it', async () => {
    const seen: unknown[] = [];
    const on = (e: Event) => { seen.push((e as CustomEvent).detail); };
    window.addEventListener('search-query', on);
    render(<HomeSearchR3 />);
    openSearch();
    await type('dune');
    fireEvent.click(screen.getByTestId('r3-person'));
    expect(seen).toEqual([{ query: 'Denis Villeneuve', tab: 'people', personId: 137427 }]);
    expect(isOpen()).toBe(false);
    window.removeEventListener('search-query', on);
  });

  it('Escape with the watchlist profile sheet open closes that sheet first, then the modal', async () => {
    render(<HomeSearchR3 />);
    openSearch();
    await type('dune');
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(screen.getAllByTestId('r3-wl-add')[0]!);
    expect(await screen.findByTestId('watchlist-add-sheet')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('watchlist-add-sheet')).toBeNull();
    expect(isOpen()).toBe(true);
    expect(screen.getByTestId('r3-results')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(isOpen()).toBe(false);
  });

  it('on the minimal build the bar has Search, Libraries and Calendar; no dead Explore or Watchlist button', () => {
    render(<HomeSearchR3 />);
    expect([...screen.getByTestId('desktop-dock').querySelectorAll('button, a')].map(el => el.textContent)).toEqual(['Search', 'Libraries', 'Calendar']);
  });
  it.each([['Calendar', '/settings/tv', ''], ['Libraries', '/settings/tv', '?view=series']])('on the minimal build %s goes to %s%s and closes the modal', (name, path, search) => {
    render(<HomeSearchR3 />);
    openSearch();
    fireEvent.click(screen.getByRole('link', { name }));
    expect([window.location.pathname, window.location.search]).toEqual([path, search]);
    expect(isOpen()).toBe(false);
  });
  it('outside the minimal build the links go to /browse, /explore, /watchlist and /settings/tv', () => {
    delete window.__LUMA_MINIMAL__;
    render(<HomeSearchR3 />);
    const seen: string[] = [];
    for (const name of ['Libraries', 'Explore', 'Watchlist', 'Calendar']) {
      fireEvent.click(screen.getByRole('link', { name }));
      seen.push(window.location.pathname);
    }
    expect(seen).toEqual(['/browse', '/explore', '/watchlist', '/settings/tv']);
    expect(screen.queryByTestId('desktop-dock')).toBeNull();    // on /settings/tv the bar is not drawn
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

describe('v245/v358: one reveal for the search modal', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  it('the modal fades on one duration and easing, with no delay on open; reduced motion makes it instant', () => {
    expect(r3Css).toContain('[data-r3-search]{--a1-dur:200ms;--a1-ease:cubic-bezier(.2,0,0,1)}');
    expect(r3Css).toContain('transition:opacity var(--a1-dur) var(--a1-ease),visibility 0s linear var(--a1-dur)}');
    expect(r3Css).toContain('.r3-modal[data-open="true"]{opacity:1;visibility:visible;transition-delay:0s}');
    expect(r3Css).toContain('transition:height var(--a1-dur) var(--a1-ease),transform var(--a1-dur) var(--a1-ease)}');
    expect(r3Css).toContain('@media (prefers-reduced-motion: reduce){[data-r3-search]{--a1-dur:0ms}}');
  });
  it('the modal and its field are always mounted and flip state in the same render (open and close)', () => {
    render(<HomeSearchR3 />);
    const m = screen.getByTestId('r3-modal'); const h = screen.getByTestId('r3-head'); const input = screen.getByTestId('search-input');
    expect(m.dataset.open).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(m.dataset.open).toBe('true');
    expect([screen.getByTestId('r3-modal'), screen.getByTestId('r3-head'), screen.getByTestId('search-input')]).toEqual([m, h, input]);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(m.dataset.open).toBe('false');
    expect(m.hasAttribute('inert')).toBe(true);
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
    expect(r3Css).toContain('.d3-chips{display:flex;flex-wrap:wrap;justify-content:center;');
    expect(r3Css).toContain('.d3-grid{display:grid;grid-template-columns:repeat(auto-fill, minmax(min(160px, 100%), 1fr));gap:28px clamp(12px, 2.1vw, 30px);');
    expect(r3Css).not.toContain('repeat(7');
    expect(r3Css).toContain('.d3-grid .r3-cardw{width:100%}');
    expect(r3Css).toMatch(/\.d3-chip\{height:36px;padding:0 16px;border-radius:8px;border:1px solid #44474e;/);
    expect(r3Css).toContain('.d3-chip[aria-pressed="true"]{background:#3a4458;border-color:#3a4458;color:#d8e2ff}');
  });
});
