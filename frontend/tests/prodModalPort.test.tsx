/** Prod port of the phone detail popup (:5002 c0930bf + 92854dd): one
 * /api/movie request per opening even when TMDb is slow, phone image sizes
 * (desktop unchanged), and phone cards that warm while visible and on touch. */
import { act, fireEvent, render, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const calls: string[] = [];
let routes: Record<string, { delay: number; body: unknown }> = {};
vi.mock('@/apiClient', () => ({
  apiFetch: (url: string) => {
    calls.push(url);
    const r = routes[url];
    const res = (ok: boolean, body: unknown) => ({
      ok, status: ok ? 200 : 404, headers: { get: () => 'application/json' },
      json: () => Promise.resolve(body), text: () => Promise.resolve(JSON.stringify(body)),
    });
    return new Promise((resolve) => setTimeout(() => resolve(r ? res(true, r.body) : res(false, {})), r?.delay ?? 5));
  },
}));

import { useMovieDetail } from '@/hooks/useMovieDetail';
import { _resetModalPrefetch, canPrefetch, warmDetail, WARM_CONCURRENCY } from '@/services/modalPrefetch';
import { _resetModalColors } from '@/services/modalColors';
import { backdropUrl, logoImg, plexBackdrop } from '@/utils/image';
import { parsePlexResponse, parsePlexTvResponse } from '@/utils/modalParsers';
import { MobilePosterCard } from '@/components/mobile/MobilePosterCard';

class FakeImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  decode = () => Promise.resolve();
  complete = false;
  naturalWidth = 0;
  set src(_v: string) { setTimeout(() => this.onload?.(), 20); }
}

const flush = async (ms: number) => {
  await act(async () => { vi.advanceTimersByTime(ms); for (let i = 0; i < 8; i++) await Promise.resolve(); });
};
const setWidth = (w: number) => Object.defineProperty(window, 'innerWidth', { configurable: true, value: w });

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('Image', FakeImage);
  calls.length = 0; routes = {};
  _resetModalPrefetch(); _resetModalColors();
  setWidth(1440);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); Object.defineProperty(navigator, 'connection', { value: undefined, configurable: true }); });

describe('one /api/movie request per opening', () => {
  const movieCalls = () => calls.filter((u) => u === '/api/movie/42').length;

  it('Plex-key opening whose TMDb lookup outlasts the short wait asks once', async () => {
    routes['/api/plex/movie/9'] = { delay: 5, body: { title: 'Heat', tmdb_id: 42 } };
    routes['/api/movie/42'] = { delay: 900, body: { id: 42, backdrop_path: '/b.jpg' } };
    renderHook(() => useMovieDetail(9, undefined));
    await flush(1500);
    expect(movieCalls()).toBe(1);
  });

  it('after a card warm-up, the opening reuses it (still one request)', async () => {
    routes['/api/plex/movie/9'] = { delay: 5, body: { title: 'Heat', tmdb_id: 42 } };
    routes['/api/movie/42'] = { delay: 5, body: { id: 42, backdrop_path: '/b.jpg' } };
    void warmDetail('movie', 9, undefined);
    await flush(100);
    renderHook(() => useMovieDetail(9, undefined));
    await flush(1000);
    expect(movieCalls()).toBe(1);
    expect(calls.filter((u) => u === '/api/plex/movie/9').length).toBe(1);
  });
});

describe('phone image sizes', () => {
  const TMDB_LOGO = 'https://image.tmdb.org/t/p/original/logo.png';

  it('phone: TMDB w780 backdrop, w300 logo on every path, sized Plex art', () => {
    setWidth(390);
    expect(backdropUrl('/b.jpg')).toContain('/w780/b.jpg');
    expect(logoImg(TMDB_LOGO)).toBe('https://image.tmdb.org/t/p/w300/logo.png');
    // Plex movie path (logo from Plex's metadata) and Plex TV path
    expect(parsePlexResponse({ title: 'x', clearlogo_url: TMDB_LOGO }).clearLogoUrl).toContain('/t/p/w300/');
    expect(parsePlexResponse({ title: 'x', backdrop_url: '/api/img/plex?url=/library/metadata/1/art/2' }).backdropUrl)
      .toContain('w=780&h=439');
    expect(plexBackdrop('/api/img/plex?url=/library/metadata/1/art/2')).toContain('w=780&h=439');
    expect(logoImg('/api/img/plex?url=/library/metadata/1/clearLogo/3')).toContain('w=300');
    expect(parsePlexTvResponse({ title: 'x', clearLogoPath: '/library/metadata/1/clearLogo/3' }).clearLogoUrl).toContain('w=300');
    expect(parsePlexTvResponse({ title: 'x', artPath: '/library/metadata/1/art/2' }).backdropUrl).toContain('w=780');
  });

  it('desktop is unchanged', () => {
    setWidth(1440);
    expect(backdropUrl('/b.jpg')).toContain('/original/b.jpg');
    expect(logoImg(TMDB_LOGO)).toBe(TMDB_LOGO);
    expect(plexBackdrop('/api/img/plex?url=/library/metadata/1/art/2')).not.toContain('w=');
  });
});

describe('phone poster cards', () => {
  let observers: { cb: IntersectionObserverCallback; el?: Element }[] = [];
  class FakeIO {
    cb: IntersectionObserverCallback;
    constructor(cb: IntersectionObserverCallback) { this.cb = cb; observers.push({ cb }); }
    observe(el: Element) { observers[observers.length - 1]!.el = el; }
    disconnect() {}
  }
  const show = (i: number, on: boolean) => act(() => {
    const o = observers[i]!;
    o.cb([{ isIntersecting: on, target: o.el! } as unknown as IntersectionObserverEntry], {} as IntersectionObserver);
  });
  beforeEach(() => { observers = []; vi.stubGlobal('IntersectionObserver', FakeIO); });

  it('warms on touch, presses, and hands its element to the tap handler', () => {
    const warm = vi.fn(() => Promise.resolve());
    const onTap = vi.fn();
    const { getByRole } = render(<MobilePosterCard posterUrl={null} title="Heat" onTap={onTap} warmKey="movie:9:" warm={warm} />);
    const btn = getByRole('button');
    expect(btn.className).toContain('luma-pressable');
    fireEvent.touchStart(btn, { touches: [{ clientX: 1, clientY: 1 }] });
    expect(warm).toHaveBeenCalled();
    fireEvent.click(btn);
    expect(onTap).toHaveBeenCalledWith(btn);
  });

  it(`visible cards warm ${WARM_CONCURRENCY} at a time; one that scrolls away first is dropped`, async () => {
    const runs: string[] = [];
    const pending: (() => void)[] = [];
    const warm = (k: string) => () => { runs.push(k); return new Promise<void>((r) => pending.push(r)); };
    render(<div>{[0, 1, 2, 3, 4, 5].map((i) => (
      <MobilePosterCard key={i} posterUrl={null} title={`t${i}`} onTap={() => {}} warmKey={`k${i}`} warm={warm(`k${i}`)} />
    ))}</div>);
    for (let i = 0; i < 6; i++) show(i, true);
    expect(runs).toEqual(['k0', 'k1', 'k2', 'k3']);
    show(4, false); // scrolled away before its turn
    await act(async () => { pending.shift()!(); for (let i = 0; i < 4; i++) await Promise.resolve(); });
    expect(runs).toEqual(['k0', 'k1', 'k2', 'k3', 'k5']);
  });

  it('the same title on two cards gets one warm-up (one key per title)', () => {
    const runs: string[] = [];
    const warm = () => { runs.push('x'); return new Promise<void>(() => {}); };
    render(<div>
      <MobilePosterCard posterUrl={null} title="a" onTap={() => {}} warmKey="movie:9:42" warm={warm} />
      <MobilePosterCard posterUrl={null} title="b" onTap={() => {}} warmKey="movie:9:42" warm={warm} />
    </div>);
    show(0, true); show(1, true);
    expect(runs.length).toBe(1);
  });

  it.each([[{ saveData: true }], [{ effectiveType: '2g' }], [{ effectiveType: 'slow-2g' }]])('no background warm-up with %o (a touch still warms)', (conn) => {
    Object.defineProperty(navigator, 'connection', { value: conn, configurable: true });
    expect(canPrefetch()).toBe(false);
    const warm = vi.fn(() => Promise.resolve());
    const { container } = render(<MobilePosterCard posterUrl={null} title="a" onTap={() => {}} warmKey="movie:1:" warm={warm} />);
    show(0, true);
    expect(warm).not.toHaveBeenCalled();
    fireEvent.touchStart(container.querySelector('button')!, { touches: [{ clientX: 1, clientY: 1 }] });
    expect(warm).toHaveBeenCalledTimes(1);
  });

  it('prefetches on 4G', () => {
    Object.defineProperty(navigator, 'connection', { value: { effectiveType: '4g', saveData: false }, configurable: true });
    expect(canPrefetch()).toBe(true);
  });
});
