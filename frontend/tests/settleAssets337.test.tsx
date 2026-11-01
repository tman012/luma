import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { renderHook, act, cleanup, render } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { Ratings } from '@/utils/modalParsers';

// Icon decodes this file controls: each URL's decode resolves only when the test says so.
const pending = new Map<string, () => void>();
class HeldImage {
  private u = '';
  set src(v: string) { this.u = v; }
  get src() { return this.u; }
  decode = () => new Promise<void>(r => { pending.set(this.u, r); });
}
vi.stubGlobal('Image', HeldImage);
const { useRatingsSettle, RATINGS_CAP_MS, RATINGS_ICON_CAP_MS } = await import('@/hooks/useRatingsSettle');
const { RATING_ICONS } = await import('@/utils/ratingIcons');
const { SkeletonCrossfade, HOME_DECODE_CAP_MS } = await import('@/components/home/Rails');
const { cardSrcSet, CARD_MAX_DENSITY } = await import('@/components/home/homeApi');
const { RatingsSettle } = await import('@/components/media/RatingsSettle');

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });
const flush = async () => { await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); }); };
const R = (p: Partial<Ratings>): Ratings => ({ imdb: null, rotten_tomatoes_critic: null, rotten_tomatoes_audience: null, metacritic: null, tmdb: null, ...p } as Ratings);
const imdb = R({ imdb: { score: 7.9 } as Ratings['imdb'] });
const mc = R({ metacritic: { score: 81 } as Ratings['metacritic'] });
type P = { v: Ratings | null; s: boolean; k: string };
const mount = (p: P) => renderHook(({ v, s, k }: P) => useRatingsSettle(v, s, k), { initialProps: p });

describe('ratings: the icons gate both reveal paths', () => {
  it('cap branch: data at the cap still waits for its icon, then reveals when it decodes', async () => {
    const h = mount({ v: imdb, s: false, k: 'a' });
    await flush();
    act(() => { vi.advanceTimersByTime(RATINGS_CAP_MS); });
    await flush();
    expect(h.result.current.shown).toBe(false);
    act(() => { pending.get(RATING_ICONS.imdb)!(); });
    await flush();
    expect(h.result.current).toEqual({ shown: true, ratings: imdb });
  });

  it('sub-cap: an icon that never decodes holds the reveal for at most RATINGS_ICON_CAP_MS', async () => {
    const h = mount({ v: mc, s: false, k: 'b' });
    await flush();
    act(() => { vi.advanceTimersByTime(RATINGS_CAP_MS); });
    await flush();
    act(() => { vi.advanceTimersByTime(RATINGS_ICON_CAP_MS - 1); });
    await flush();
    expect(h.result.current.shown).toBe(false);
    act(() => { vi.advanceTimersByTime(1); });
    await flush();
    expect(h.result.current).toEqual({ shown: true, ratings: mc });
  });

  it('settled branch waits on icons too (sub-cap from when the data is ready)', async () => {
    const tm = R({ rotten_tomatoes_audience: { score: 90 } as Ratings['rotten_tomatoes_audience'] });
    const h = mount({ v: tm, s: true, k: 'c' });
    await flush();
    expect(h.result.current.shown).toBe(false);
    act(() => { vi.advanceTimersByTime(RATINGS_ICON_CAP_MS); });
    await flush();
    expect(h.result.current.shown).toBe(true);
  });

  it('boot preloads every rating icon; the service worker serves versioned icons cache-first', () => {
    expect(src('main.tsx')).toMatch(/preloadRatingIcons\(\)/);
    const sw = readFileSync(resolve(__dirname, '../../static/sw.js'), 'utf8');
    expect(sw).toMatch(/path\.startsWith\('\/static\/images\/'\) && url\.searchParams\.has\('v'\)[\s\S]{0,80}caches\.match\(event\.request\)/);
  });

  it('the TV season view ratings go through the settle latch', () => {
    const s = src('components/media/TvDetailModal/ExpandedLayout.tsx');
    expect(s).toMatch(/useRatingsSettle\(seasonRatingsState\.ratings/);
    expect(s).toMatch(/<RatingsSettle settle=\{seasonRatings\}/);
    expect(s).not.toMatch(/<RatingsBar /);
  });
});

function railsWithPoster() {
  return <div className="luma-rails-in"><img alt="" loading="lazy" src="/p.jpg" /></div>;
}
function stubDecode(impl: () => Promise<void>) {
  Object.defineProperty(HTMLImageElement.prototype, 'decode', { value: impl, configurable: true, writable: true });
  return { mockRestore: () => { delete (HTMLImageElement.prototype as { decode?: unknown }).decode; } };
}
function inViewport() {
  return vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ top: 10, left: 10, bottom: 200, right: 120, width: 110, height: 190, x: 10, y: 10, toJSON: () => ({}) } as DOMRect);
}

describe('ratings row: text and icons first paint together', () => {
  it('the fade waits for the row\'s own icon elements to decode, at most RATINGS_ICON_CAP_MS', async () => {
    let done!: () => void;
    const dec = stubDecode(() => new Promise<void>(r => { done = r; }));
    const { container } = render(<RatingsSettle settle={{ shown: true, ratings: imdb }} size="lg" reserve={0} />);
    const slot = container.querySelector('[data-ratings-slot]') as HTMLElement;
    expect(slot.style.opacity).toBe('0');
    await act(async () => { done(); await Promise.resolve(); await Promise.resolve(); });
    expect(slot.style.opacity).toBe('1');
    cleanup();
    stubDecode(() => new Promise<void>(() => {}));
    const r2 = render(<RatingsSettle settle={{ shown: true, ratings: imdb }} size="lg" reserve={0} />);
    const s2 = r2.container.querySelector('[data-ratings-slot]') as HTMLElement;
    act(() => { vi.advanceTimersByTime(RATINGS_ICON_CAP_MS - 1); });
    expect(s2.style.opacity).toBe('0');
    act(() => { vi.advanceTimersByTime(1); });
    expect(s2.style.opacity).toBe('1');
    dec.mockRestore();
  });
});

describe('Home: the reveal waits for the first viewport posters', () => {
  it('holds the skeleton until the posters decode, then reveals; first viewport cards load eager, high priority', async () => {
    const spy = inViewport();
    let done!: () => void;
    const dec = stubDecode(() => new Promise<void>(r => { done = r; }));
    const { rerender, container } = render(<SkeletonCrossfade loading><span /></SkeletonCrossfade>);
    rerender(<SkeletonCrossfade loading={false}>{railsWithPoster()}</SkeletonCrossfade>);
    const img = container.querySelector('.luma-rails-in img') as HTMLImageElement;
    expect(img.loading).toBe('eager');
    expect(img.getAttribute('fetchpriority')).toBe('high');
    expect(container.firstElementChild!.hasAttribute('data-xf-hold')).toBe(true);
    act(() => { vi.advanceTimersByTime(HOME_DECODE_CAP_MS - 100); });
    expect(container.firstElementChild!.hasAttribute('data-xf-hold')).toBe(true);
    await act(async () => { done(); await Promise.resolve(); await Promise.resolve(); });
    expect(container.firstElementChild!.hasAttribute('data-xf-hold')).toBe(false);
    act(() => { vi.advanceTimersByTime(300); });
    expect(container.querySelector('[data-testid="home-skeleton-out"]')).toBeNull();
    spy.mockRestore(); dec.mockRestore();
  });

  it('a poster that never decodes: the reveal goes at HOME_DECODE_CAP_MS (800ms)', async () => {
    const spy = inViewport();
    const dec = stubDecode(() => new Promise<void>(() => {}));
    const { rerender, container } = render(<SkeletonCrossfade loading><span /></SkeletonCrossfade>);
    rerender(<SkeletonCrossfade loading={false}>{railsWithPoster()}</SkeletonCrossfade>);
    expect(HOME_DECODE_CAP_MS).toBe(800);
    act(() => { vi.advanceTimersByTime(HOME_DECODE_CAP_MS - 1); });
    expect(container.firstElementChild!.hasAttribute('data-xf-hold')).toBe(true);
    act(() => { vi.advanceTimersByTime(1); });
    expect(container.firstElementChild!.hasAttribute('data-xf-hold')).toBe(false);
    spy.mockRestore(); dec.mockRestore();
  });

  it('srcset stops at the candidate covering the largest card box at 2x', () => {
    expect(CARD_MAX_DENSITY).toBe(2);
    expect(cardSrcSet('/api/img/tmdb?path=%2Fp.jpg&size=w342')).toBe('/api/img/tmdb?path=%2Fp.jpg&size=w342 342w');
    expect(cardSrcSet('/api/img/tmdb?path=%2Fb.jpg&size=w300', 'wide')).toBe(
      '/api/img/tmdb?path=%2Fb.jpg&size=w300 300w, /api/img/tmdb?path=%2Fb.jpg&size=w780 780w');
    expect(cardSrcSet('/api/img/plex?url=%2Fl&w=300')).toBe('/api/img/plex?url=%2Fl&w=300 300w, /api/img/plex?url=%2Fl&w=500 500w');
    expect(cardSrcSet('/api/img/plex?url=%2Fa&w=480', 'wide')).toBe('/api/img/plex?url=%2Fa&w=480 480w, /api/img/plex?url=%2Fa&w=960 960w');
  });
});
