/** Movie and TV sheets open on the tap into a layout-stable frame and reveal the title and artwork into it once
 *  they are decoded; they are never held for the network and never abandoned by a timer. A timer does not open a
 *  color wash. Startup / full refresh is the navy Loading… screen; a later refetch does not blank rows that are
 *  already on screen. */
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const layoutMode = vi.hoisted(() => ({ mode: 'compact' as 'compact' | 'expanded' }));
vi.mock('@/contexts/LayoutModeContext', async (orig) => ({
  ...(await orig<typeof import('@/contexts/LayoutModeContext')>()),
  useLayoutMode: () => ({ mode: layoutMode.mode, isCompact: layoutMode.mode === 'compact', isExpanded: layoutMode.mode === 'expanded', layoutMode: layoutMode.mode }),
}));
const auth = vi.hoisted(() => ({
  authenticated: true, loading: false, needsStart: false, startInProgress: false, pinUnlock: false,
  user: { id: 1, name: 'taylor', username: 'taylor', is_admin: true, isAdmin: true }, implicit: false, isAdmin: true, logout: vi.fn(),
}));
vi.mock('@/contexts/AuthContext', async (orig) => ({
  ...(await orig<typeof import('@/contexts/AuthContext')>()),
  useAuth: () => auth,
}));
vi.mock('@/contexts/ModalHostContext', async (orig) => ({
  ...(await orig<typeof import('@/contexts/ModalHostContext')>()),
  useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn(), registerBackHandler: vi.fn(() => () => {}), closeAll: vi.fn() }),
}));

import { MovieDetailModal } from '@/components/media/MovieDetailModal';
import { MinimalHomePage } from '@/pages/MinimalHomePage';
import { detailSheetReady, SHEET_OPEN_BOUND_MS } from '@/loading/sheetOpenGate';
import { SHEET_EXIT_MS } from '@/components/layout/SheetFrame';
import { _resetImageLoader } from '@/loading/imageLoader';
import { _resetModalPrefetch } from '@/services/modalPrefetch';
import { STARTUP_LOADING_TEXT } from '@/components/start/StartupLoading';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const sheet = () => document.querySelector('[data-modal="MovieDetailModal"]');

type Held = { ok: (v: boolean) => void };
const held: Held[] = [];
class FakeImage {
  decoding = '';
  fetchPriority = '';
  onload: null | (() => void) = null;
  onerror: null | (() => void) = null;
  private s = '';
  set src(v: string) { this.s = v; }
  get src() { return this.s; }
  removeAttribute() { this.s = ''; }
  decode() {
    return new Promise<void>((res, rej) => {
      held.push({ ok: (v) => (v ? res() : rej(new Error('fail'))) });
    });
  }
}
async function release(ok = true) {
  const batch = held.splice(0, held.length);
  await act(async () => {
    for (const h of batch) h.ok(ok);
    await Promise.resolve();
    await Promise.resolve();
  });
}

const MOVIE = { id: 603, title: 'The Matrix', year: 1999, backdrop_path: '/m.jpg', poster_path: '/p.jpg', videos: { results: [] } };
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });

function installFetch(movie: () => Response | Promise<Response> = () => json(MOVIE)) {
  vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u);
    if (url.includes('/api/movie/680')) return json({ id: 680, title: 'Pulp Fiction', release_date: '1994-10-14', backdrop_path: '/pf.jpg', poster_path: '/pfp.jpg', videos: { results: [] } });
    if (url.includes('/api/movie/')) return movie();
    if (url.includes('/api/clearlogo/')) return json({ clearlogo_url: null });
    if (url.includes('/api/plex/lookup')) return json({ owned: false });
    return json({});
  }));
}

beforeEach(() => {
  layoutMode.mode = 'compact';
  auth.authenticated = true; auth.loading = false;
  held.length = 0;
  _resetImageLoader();
  _resetModalPrefetch();
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('detail sheet readiness boundaries', () => {
  it('requires a real title, a settled hero and known ownership; an error with no detail is the existing failure panel', () => {
    expect(detailSheetReady(true, false, false, null, 'hold', false)).toBe(false);
    expect(detailSheetReady(false, false, true, '  ', 'text', true)).toBe(false);
    expect(detailSheetReady(false, false, true, 'Heat', 'hold', true)).toBe(false);
    expect(detailSheetReady(false, false, true, 'Heat', 'text', false)).toBe(false);
    expect(detailSheetReady(false, false, true, 'Heat', 'logo', true)).toBe(true);
    expect(detailSheetReady(false, false, true, 'Heat', 'text', true)).toBe(true);
    expect(detailSheetReady(false, true, false, null, 'hold', false)).toBe(true);
  });

  const gate = () => document.querySelector('[data-reveal-gate]')?.getAttribute('data-reveal-gate');

  it('opens before any data and is not abandoned when the open bound passes', async () => {
    vi.useFakeTimers();
    installFetch(() => new Promise<Response>(() => {}));
    const onClose = vi.fn();
    render(<MovieDetailModal tmdbId={603} onClose={onClose} />);
    expect(sheet()).not.toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(SHEET_OPEN_BOUND_MS + SHEET_EXIT_MS + 200); });
    expect(sheet()).not.toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('shows the frame at once, reveals the decoded title into it, and holds no color wash before that', async () => {
    installFetch();
    render(<MovieDetailModal tmdbId={603} onClose={() => {}} />);
    expect(sheet()).not.toBeNull();
    await waitFor(() => expect(held.length).toBeGreaterThan(0));
    expect(gate()).not.toBe('shown');
    await release(true);
    await waitFor(() => expect(gate()).toBe('shown'));
    expect(screen.getByText('The Matrix')).toBeTruthy();
  });

  it('a failed backdrop still reveals once, on the text title', async () => {
    installFetch();
    render(<MovieDetailModal tmdbId={603} onClose={() => {}} />);
    await waitFor(() => expect(held.length).toBeGreaterThan(0));
    expect(gate()).not.toBe('shown');
    await release(false);
    await waitFor(() => expect(gate()).toBe('shown'));
    expect(screen.getByText('The Matrix')).toBeTruthy();
  });

  it('a failed detail request shows the existing error panel in the open sheet', async () => {
    installFetch(() => json({ detail: 'nope' }, 500));
    render(<MovieDetailModal tmdbId={603} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText('Couldn’t load details')).toBeTruthy());
    expect(sheet()).not.toBeNull();
    expect(screen.queryByText('The Matrix')).toBeNull();
  });

  it('unmount cancels the load so a late answer cannot paint', async () => {
    let resolveMovie: (r: Response) => void = () => {};
    installFetch(() => new Promise<Response>((res) => { resolveMovie = res; }));
    const view = render(<MovieDetailModal tmdbId={603} onClose={() => {}} />);
    view.unmount();
    await act(async () => { resolveMovie(json(MOVIE)); await Promise.resolve(); });
    expect(sheet()).toBeNull();
    expect(screen.queryByText('The Matrix')).toBeNull();
  });

  it('a rapid switch never shows the title that was abandoned', async () => {
    installFetch();
    const first = render(<MovieDetailModal tmdbId={603} onClose={() => {}} />);
    await waitFor(() => expect(held.length).toBeGreaterThan(0));
    first.unmount();
    const pending = held.length;
    render(<MovieDetailModal tmdbId={680} onClose={() => {}} />);
    expect(sheet()).not.toBeNull();
    await waitFor(() => expect(held.length).toBeGreaterThan(pending));
    await release(true);
    await waitFor(() => expect(gate()).toBe('shown'));
    expect(screen.queryByText('The Matrix')).toBeNull();
    expect(screen.getByText('Pulp Fiction')).toBeTruthy();
  });

  it('close then reopen opens the frame at once and reveals only when the new art is decoded', async () => {
    installFetch();
    const first = render(<MovieDetailModal tmdbId={603} onClose={() => {}} />);
    await waitFor(() => expect(held.length).toBeGreaterThan(0));
    await release(true);
    await waitFor(() => expect(gate()).toBe('shown'));
    first.unmount();
    expect(sheet()).toBeNull();
    _resetImageLoader();
    held.length = 0;
    render(<MovieDetailModal tmdbId={603} onClose={() => {}} />);
    expect(sheet()).not.toBeNull();
    await waitFor(() => expect(held.length).toBeGreaterThan(0));
    expect(gate()).not.toBe('shown');
    await release(true);
    await waitFor(() => expect(gate()).toBe('shown'));
    expect(screen.getByText('The Matrix')).toBeTruthy();
  });
});

describe('entry paths still reach the sheet', () => {
  it('home, continue watching and search open through openWhenReady; the library calendar opens the TV sheet', () => {
    const home = src('pages/MinimalHomePage.tsx');
    expect(home).toContain('openWhenReady');
    expect(home).toContain('continue_watching');
    expect(src('services/searchApi.ts')).toContain('return openWhenReady(');
    expect(src('components/search/HomeSearchR3.tsx')).toContain('openResult(');
    expect(src('components/mobile/MobileSearchOverlay.tsx')).toContain('openWhenReady');
    expect(src('components/library/TvCalendar.tsx')).toContain('host.openTv(');
    for (const f of ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/MovieDetailModal/ExpandedLayout.tsx', 'components/media/TvDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/ExpandedLayout.tsx']) {
      if (f.includes('Compact')) expect(src(f), f).toContain('detailSheetReady');
      expect(src(f), f).not.toContain('useUnopenedClose');
    }
  });
});

const HOME = {
  plex: true,
  continue_watching: [],
  rails: [{ id: '1', title: 'Movies', type: 'movie', items: [{ key: '21', type: 'movie', title: 'Heat', subtitle: '1995', poster: null }] }],
};

describe('startup and background refresh', () => {
  it('a first load shows Loading… and not a skeleton or an empty home', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    const el = screen.getByTestId('startup-loading');
    expect(el.textContent).toContain(STARTUP_LOADING_TEXT);
    expect(STARTUP_LOADING_TEXT).toBe('Loading\u2026');
    const spin = el.querySelector('.luma-spin') as HTMLElement;
    expect(parseFloat(spin.style.width)).toBeGreaterThanOrEqual(40);
    expect(parseFloat(spin.style.height)).toBeGreaterThanOrEqual(40);
    expect(parseFloat((el.querySelector('p') as HTMLElement).style.fontSize)).toBeGreaterThanOrEqual(28);
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
    expect(screen.queryByText('Nothing here yet')).toBeNull();
    expect(screen.queryByTestId('home-rails')).toBeNull();
  });

  it('the document splash reuses the navy glow and the same Loading… word', () => {
    const html = readFileSync(resolve(__dirname, '../index.html'), 'utf8');
    expect(html).toContain('Loading\u2026');
    expect(html).toMatch(/#splash-ring\s*\{[^}]*width:\s*48px/);
    expect(html).toMatch(/font:\s*600 32px/);
    expect(html).toContain('#262a66 0%, #15102a 55%, #0a0818 100%');
    expect(html).toContain('prefers-reduced-motion: reduce');
    expect(src('components/start/StartUI.tsx')).toContain('.luma-spin,.luma-start-fade{animation:none}');
    expect(src('main.tsx')).toContain("addEventListener('luma:content-ready'");
    expect(src('main.tsx')).not.toMatch(/requestAnimationFrame\(\(\) => \{\s*document\.getElementById\('splash'\)/);
    expect(src('App.tsx')).toContain('signalContentReady');
  });

  it('a refetch after rows are on screen does not blank them for the startup screen', async () => {
    let matches = false;
    const listeners = new Set<() => void>();
    vi.stubGlobal('matchMedia', (q: string) => ({
      get matches() { return q.includes('prefers-reduced-motion') ? false : matches; },
      media: q,
      addEventListener: (_e: string, fn: () => void) => { listeners.add(fn); },
      removeEventListener: (_e: string, fn: () => void) => { listeners.delete(fn); },
      addListener: (fn: () => void) => { listeners.add(fn); },
      removeListener: (fn: () => void) => { listeners.delete(fn); },
    }));
    let hang = false;
    vi.stubGlobal('fetch', vi.fn(() => (hang ? new Promise(() => {}) : Promise.resolve(json(HOME)))));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
    hang = true;
    matches = true;
    await act(async () => { listeners.forEach((fn) => fn()); });
    matches = false;
    await act(async () => { listeners.forEach((fn) => fn()); });
    expect(screen.getByTestId('home-rails')).toBeTruthy();
    expect(screen.getByText('Heat')).toBeTruthy();
    expect(screen.queryByTestId('startup-loading')).toBeNull();
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
  });
});
