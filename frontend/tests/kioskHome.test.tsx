import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The voice module primes an AudioContext on the first click; jsdom has none.
class FakeAudioContext { state = 'running'; resume() { return Promise.resolve(); } createBuffer() { return {}; } createBufferSource() { return { connect() {}, start() {}, buffer: null }; } destination = {}; }
(globalThis as unknown as { AudioContext: unknown }).AudioContext ??= FakeAudioContext;

const auth = { authenticated: true, loading: false, needsStart: false, startInProgress: false, pinUnlock: false,
  user: { id: 1, name: 'taylor', is_admin: true }, implicit: false, logout: vi.fn() };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
const modals = { openMovie: vi.fn(), openTv: vi.fn(), registerBackHandler: vi.fn() };
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => modals }));

import { MinimalHomePage } from '@/pages/MinimalHomePage';
import { TVModeProvider } from '@/contexts/TVModeContext';
import { LayoutModeProvider } from '@/contexts/LayoutModeContext';
import { SpatialNavProvider } from '@/components/tv/SpatialNavProvider';

const movie = (i: number, art = true) => ({
  rating_key: 100 + i, title: `Movie ${i}`, year: 2026, summary: `Plot ${i}.`, content_rating: 'PG-13',
  duration: 8040000, genres: ['Drama', 'Thriller'], backdrop_url: art ? `/api/img/plex?url=/library/metadata/${100 + i}/art/1` : null,
});
const TEN = Array.from({ length: 10 }, (_, i) => movie(i));
const json = (body: unknown, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => body }) as Response;

let wide = true;
let reduced = false;
function stubMatchMedia() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px') ? wide : q.includes('prefers-reduced-motion') ? reduced : false,
    media: q, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }));
}

function renderHome() {
  return render(
    <MemoryRouter>
      <TVModeProvider><LayoutModeProvider><SpatialNavProvider>
        <MinimalHomePage />
      </SpatialNavProvider></LayoutModeProvider></TVModeProvider>
    </MemoryRouter>,
  );
}

function setHidden(v: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => v });
  document.dispatchEvent(new Event('visibilitychange'));
}

describe('web kiosk home (>=1024px)', () => {
  beforeEach(() => { wide = true; reduced = false; stubMatchMedia(); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); setHidden(false); });

  it('shows the spinner on solid navy, then the slideshow; only /api/hero/content is called', async () => {
    let resolve!: (r: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>(r => { resolve = r; }));
    vi.stubGlobal('fetch', fetchMock);
    renderHome();
    expect(screen.getByTestId('kiosk-loading')).toBeTruthy();
    expect(screen.getByTestId('kiosk-loading').textContent).toBe('');
    expect(screen.getByTestId('kiosk-home').style.background).toMatch(/#15102a|rgb\(21, 16, 42\)/);
    await act(async () => { resolve(json({ success: true, movies: TEN })); });
    await waitFor(() => screen.getByText('Movie 0'));
    const urls = fetchMock.mock.calls.map(c => String((c as unknown[])[0]));
    expect(urls.every(u => u.includes('/api/hero/content'))).toBe(true);
    expect(screen.getByText('2026 · 2h 14m · PG-13')).toBeTruthy();
    expect(screen.getByText('Plot 0.')).toBeTruthy();
    expect(screen.getAllByText('Drama')).toHaveLength(1);
  });

  it('has no rails, no scroll, and the header overlays it', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ success: true, movies: TEN })));
    renderHome();
    await waitFor(() => screen.getByText('Movie 0'));
    expect(screen.queryByTestId('home-rails')).toBeNull();
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
    expect(document.documentElement.style.overflow).toBe('');   // global lock stays on: no page scroll
    const stage = screen.getByTestId('kiosk-home');
    expect(stage.style.position).toBe('fixed');
    expect(stage.style.overflow).toBe('hidden');
    const header = screen.getByTestId('kiosk-header');
    expect(header.style.position).toBe('fixed');
    expect(header.style.background).toContain('rgba(0, 0, 0, 0.8)');
    expect(header.style.padding).toBe('20px 60px 20px 44px');
    expect((header.querySelector('img') as HTMLImageElement).style.height).toBe('64px');
    expect(screen.getByRole('button', { name: 'Account' }).style.width).toBe('48px');
  });

  it('only the title (or logo art), search and the avatar are interactive; the title opens the popup', async () => {
    modals.openMovie.mockClear();
    vi.stubGlobal('fetch', vi.fn(async () => json({ success: true, movies: TEN })));
    const { container } = renderHome();
    await waitFor(() => screen.getByText('Movie 0'));
    const focusable = container.querySelectorAll('button, a[href], [tabindex]:not([tabindex="-1"]), [role="button"], input, select, textarea');
    // v215 C2: search is the hero pill after the header (logo + avatar only); the round header button is gone.
    expect(container.querySelector('[data-testid="kiosk-header"] button[aria-label="Search"]')).toBeNull();
    expect([...focusable].map(e => e.getAttribute('aria-label') || e.textContent)).toEqual(['Movie 0', 'Account', 'Search']);
    const hero = container.querySelector('[data-hero-display-only]') as HTMLElement;
    // The backdrop, plot and genres open nothing; a drag doesn't swipe.
    fireEvent.click(hero);
    fireEvent.click(screen.getByText('Plot 0.'));
    fireEvent.mouseDown(hero, { clientX: 600 }); fireEvent.mouseUp(hero, { clientX: 100 });
    expect(modals.openMovie).not.toHaveBeenCalled();
    // The title is a real button: click, Enter and Space all open it.
    const title = screen.getByRole('button', { name: 'Movie 0' });
    expect(title.tagName).toBe('BUTTON');
    expect(title.className).toContain('pointer-events-auto');
    fireEvent.click(title);
    expect(modals.openMovie).toHaveBeenCalledTimes(1);
    expect(modals.openMovie.mock.calls[0][0]).toMatchObject({ ratingKey: 100 });
    modals.openMovie.mockClear();
    expect(screen.getByText('Movie 0')).toBeTruthy();   // no swipe to the next one
    expect(container.querySelector('[data-hero-content]')!.className).toContain('pointer-events-none');
  });

  it('rotates every 8 s through the movies and pauses while the tab is hidden', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.stubGlobal('fetch', vi.fn(async () => json({ success: true, movies: TEN })));
    renderHome();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText('Movie 0')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(7999); });
    expect(screen.getByText('Movie 0')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1 + 6000); });   // + kiosk preload timeout (image never loads in jsdom)
    expect(screen.getByText('Movie 1')).toBeTruthy();
    act(() => setHidden(true));
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByText('Movie 1')).toBeTruthy();
    act(() => setHidden(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(8001); });   // interval restarts
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });   // preload (6 s cap), then swap
    expect(screen.getByText('Movie 2')).toBeTruthy();
  });

  it('keeps rotating through several full cycles, wrapping, even across re-renders', async () => {
    // Backdrops load (as real proxied images do): each slide lasts exactly 8 s.
    class LoadingImage { onload: (() => void) | null = null; onerror: (() => void) | null = null;
      set src(_v: string) { setTimeout(() => this.onload?.(), 50); } }
    vi.stubGlobal('Image', LoadingImage);
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.stubGlobal('fetch', vi.fn(async () => json({ success: true, movies: TEN })));
    renderHome();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText('Movie 0')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });   // offset past each 50 ms preload
    for (let step = 1; step <= 23; step++) {   // 2.3 full cycles of 10
      if (step % 3 === 0) {
        // A parent re-render mid-slide (avatar menu open/close) must not reset the timer.
        await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
        fireEvent.click(screen.getByRole('button', { name: 'Account' }));
        fireEvent.click(screen.getByRole('button', { name: 'Account' }));
        await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
      } else {
        await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
      }
      expect(screen.getByText(`Movie ${step % 10}`)).toBeTruthy();
    }
    // Only one hero fetch: the kiosk never remounted.
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1);
  });

  it('logo and avatar only; the avatar dropdown holds Settings (admins) and Sign out', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ success: true, movies: TEN })));
    renderHome();
    await waitFor(() => screen.getByText('Movie 0'));
    expect(screen.queryByRole('button', { name: 'Menu' })).toBeNull();
    expect(screen.queryByTestId('app-sidebar')).toBeNull();
    expect(screen.getByTestId('kiosk-home').style.inset).toBe('0px');   // full-bleed
    fireEvent.click(screen.getByRole('button', { name: 'Account' }));
    const menu = screen.getByTestId('user-menu');
    expect(menu.style.minWidth).toBe('200px');
    expect(menu.style.top).toBe('56px');
    const items = screen.getAllByRole('menuitem');
    expect(items.map(i => i.textContent?.trim())).toEqual(['Settings', 'Sign out']);
    expect(items[0]!.style.height).toBe('44px');
    expect(screen.getByRole('separator')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('user-menu')).toBeNull());
  });

  it('crossfades over 1.2 s, instantly under reduced motion', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ success: true, movies: TEN })));
    const view = renderHome();
    await waitFor(() => screen.getByText('Movie 0'));
    const layers = () => [...view.container.querySelectorAll('[data-hero-display-only] .duration-\\[1200ms\\]')] as HTMLElement[];
    expect(layers()).toHaveLength(2);
    expect(layers().every(l => l.style.transition === '')).toBe(true);
    cleanup();
    reduced = true; stubMatchMedia();
    const v2 = renderHome();
    await waitFor(() => screen.getByText('Movie 0'));
    const l2 = [...v2.container.querySelectorAll('[data-hero-display-only] .duration-\\[1200ms\\]')] as HTMLElement[];
    expect(l2.every(l => l.style.transition.startsWith('none'))).toBe(true);
  });

  it('empty or art-less data is solid navy with the header only', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ success: true, movies: [movie(1, false)] })));
    renderHome();
    await waitFor(() => screen.getByTestId('kiosk-empty'));
    expect(screen.queryByTestId('kiosk-loading')).toBeNull();
    expect(screen.queryByText('Movie 1')).toBeNull();
    expect(screen.getByTestId('kiosk-header')).toBeTruthy();
  });

  it('a failed call is empty too (no error text)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({}, false)));
    renderHome();
    await waitFor(() => screen.getByTestId('kiosk-empty'));
    expect(screen.getByTestId('kiosk-home').textContent).toBe('');
  });
});

describe('phones keep #11 (<1024px)', () => {
  beforeEach(() => { wide = false; stubMatchMedia(); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('renders the rails and never asks for hero content', async () => {
    const fetchMock = vi.fn(async () => json({ plex: true, continue_watching: [], rails: [{ id: '1', title: 'Movies', type: 'movie', items: [{ key: '21', type: 'movie', title: 'Heat', subtitle: '1995', poster: null }] }] }));
    vi.stubGlobal('fetch', fetchMock);
    renderHome();
    await waitFor(() => screen.getByTestId('home-rails'));
    expect(screen.queryByTestId('kiosk-home')).toBeNull();
    expect(fetchMock.mock.calls.map(c => String((c as unknown[])[0])).some(u => u.includes('/api/hero'))).toBe(false);
    expect(document.documentElement.style.overflow).toBe('auto');
  });
});

describe('v227 R3 on Home: desktop and tablet only, phone unchanged', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  it('>= 1024px: the R3 Search title and field, no C2 pill', async () => {
    wide = true; stubMatchMedia();
    vi.stubGlobal('fetch', vi.fn(async () => json({ success: true, movies: TEN })));
    renderHome();
    await waitFor(() => screen.getByText('Movie 0'));
    expect(screen.getByTestId('r3-search')).toBeTruthy();
    expect(screen.queryByTestId('home-search-pill')).toBeNull();
  });
  it('phone: no R3, the header search button as before', async () => {
    wide = false; stubMatchMedia();
    vi.stubGlobal('fetch', vi.fn(async () => json({ continue_watching: [], rails: [], plex: true })));
    renderHome();
    expect(screen.getByTestId('search-button')).toBeTruthy();
    expect(screen.queryByTestId('r3-search')).toBeNull();
    expect(screen.queryByTestId('home-search-pill')).toBeNull();
  });
});
