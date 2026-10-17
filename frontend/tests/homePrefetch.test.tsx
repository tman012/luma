import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HomeRails } from '@/components/home/Rails';
import { _resetModalPrefetch, canPrefetch } from '@/services/modalPrefetch';
import { _resetModalColors } from '@/services/modalColors';
import type { Card, Rail } from '@/components/home/homeApi';

// IntersectionObserver we drive by hand.
const observers: { cb: IntersectionObserverCallback; el: Element | null }[] = [];
class FakeIO {
  cb: IntersectionObserverCallback;
  rec: { cb: IntersectionObserverCallback; el: Element | null };
  constructor(cb: IntersectionObserverCallback) { this.cb = cb; this.rec = { cb, el: null }; observers.push(this.rec); }
  observe(el: Element) { this.rec.el = el; }
  disconnect() { this.rec.el = null; }
  unobserve() {}
  takeRecords() { return []; }
}
const visible = (testId: string, on = true) => {
  const rec = observers.find((o) => o.el?.getAttribute('data-testid') === testId)!;
  rec.cb([{ isIntersecting: on, target: rec.el } as unknown as IntersectionObserverEntry], {} as IntersectionObserver);
};

const card = (key: string, tmdb?: number): Card => ({ key, type: 'movie', title: `T${key}`, poster: null, ...(tmdb ? { tmdb_id: tmdb } : {}) } as Card);
const rails = (cards: Card[]): Rail[] => [{ id: '1', title: 'Movies', type: 'movie', items: cards } as Rail];

let pending: (() => void)[] = [];
let detailCalls: string[] = [];

describe('Home rails prefetch the detail of cards on screen', () => {
  beforeEach(() => {
    _resetModalPrefetch(); _resetModalColors();
    observers.length = 0; pending = []; detailCalls = [];
    vi.stubGlobal('IntersectionObserver', FakeIO);
    vi.stubGlobal('Image', class { set src(_v: string) {} decode() { return Promise.resolve(); } });
    vi.stubGlobal('fetch', vi.fn((u: string) => {
      const url = String(u);
      if (url.includes('/api/plex/movie/') || url.includes('/api/movie/')) detailCalls.push(url);
      // Detail calls stay open until released, so the 4-at-a-time limit shows.
      return new Promise<Response>((res) => pending.push(() => res(new Response('{}', { status: 200 }))));
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); Object.defineProperty(navigator, 'connection', { value: undefined, configurable: true }); });

  it('warms a card when it comes on screen, at most 4 at a time, and skips cards that scrolled away first', async () => {
    const cards = ['1', '2', '3', '4', '5', '6'].map((k) => card(k));
    render(<HomeRails cw={[]} rails={rails(cards)} onOpen={() => {}} />);
    expect(detailCalls).toEqual([]);
    await act(async () => { for (const k of ['1', '2', '3', '4', '5', '6']) visible(`card-${k}`); });
    expect(detailCalls.map((u) => u.split('?')[0])).toEqual(['/api/plex/movie/1', '/api/plex/movie/2', '/api/plex/movie/3', '/api/plex/movie/4']);
    await act(async () => { visible('card-5', false); }); // scrolled away before its turn
    // Let the first jobs finish (their detail, logo and color calls).
    for (let i = 0; i < 10; i++) {
      await act(async () => { const p = pending; pending = []; p.forEach((r) => r()); await new Promise((r) => setTimeout(r, 0)); });
    }
    const urls = detailCalls.map((u) => u.split('?')[0]);
    expect(urls).toContain('/api/plex/movie/6');
    expect(urls).not.toContain('/api/plex/movie/5');
  });

  it('dedupes through the detail cache: the same title in two places is fetched once', async () => {
    render(<HomeRails cw={[card('a', 77)]} rails={rails([card('b', 77)])} onOpen={() => {}} />);
    await act(async () => { visible('card-a'); visible('card-b'); });
    expect(detailCalls.filter((u) => u.includes('/api/movie/77')).length).toBe(1);
  });

  it.each([[{ saveData: true }], [{ effectiveType: '2g' }], [{ effectiveType: 'slow-2g' }]])('does not prefetch with %o', async (conn) => {
    Object.defineProperty(navigator, 'connection', { value: conn, configurable: true });
    expect(canPrefetch()).toBe(false);
    render(<HomeRails cw={[]} rails={rails([card('1')])} onOpen={() => {}} />);
    await act(async () => { visible('card-1'); });
    expect(detailCalls).toEqual([]);
  });

  it('prefetches on 4G', () => {
    Object.defineProperty(navigator, 'connection', { value: { effectiveType: '4g', saveData: false }, configurable: true });
    expect(canPrefetch()).toBe(true);
  });
});
