import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import {
  _resetModalPrefetch, canPrefetch, scheduleWarm, resetWarmGroup, warmDetail, UNKNOWN_LINK_CAP, WARM_CONCURRENCY,
} from '@/services/modalPrefetch';
import { warmResults, SEARCH_WARM_DEBOUNCE_MS, type SearchMovie } from '@/services/searchApi';

const setConn = (c: object | undefined) => Object.defineProperty(navigator, 'connection', { value: c, configurable: true });
const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const never = () => new Promise<void>(() => {});

beforeEach(() => { _resetModalPrefetch(); setConn({ effectiveType: '4g' }); });
afterEach(() => { setConn(undefined); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('canPrefetch', () => {
  it('skips on saveData, 2g, metered or cellular; allows a fast unmetered link or no API', () => {
    setConn({ saveData: true, effectiveType: '4g' }); expect(canPrefetch()).toBe(false);
    setConn({ effectiveType: '2g' }); expect(canPrefetch()).toBe(false);
    setConn({ effectiveType: 'slow-2g' }); expect(canPrefetch()).toBe(false);
    setConn({ effectiveType: '4g', metered: true }); expect(canPrefetch()).toBe(false);
    setConn({ effectiveType: '4g', type: 'cellular' }); expect(canPrefetch()).toBe(false);
    setConn({ effectiveType: '4g', type: 'wifi' }); expect(canPrefetch()).toBe(true);
    setConn(undefined); expect(canPrefetch()).toBe(true);
  });
  it('a skipped link queues nothing', () => {
    setConn({ saveData: true });
    const run = vi.fn(never);
    scheduleWarm('a', run, 'home');
    expect(run).not.toHaveBeenCalled();
  });
});

describe('background warm limits', () => {
  it('at most WARM_CONCURRENCY (4) at a time; the next starts when one finishes', async () => {
    const done: (() => void)[] = [];
    const runs = Array.from({ length: 6 }, () => vi.fn(() => new Promise<void>((r) => { done.push(r); })));
    runs.forEach((r, i) => scheduleWarm(`k${i}`, r));
    expect(WARM_CONCURRENCY).toBe(4);
    expect(runs.filter((r) => r.mock.calls.length).length).toBe(4);
    done[0]!(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(runs.filter((r) => r.mock.calls.length).length).toBe(5);
  });
  it('no Network Information API (iOS): each surface warms at most UNKNOWN_LINK_CAP in the background', () => {
    setConn(undefined);
    const runs = Array.from({ length: UNKNOWN_LINK_CAP + 3 }, () => vi.fn(async () => {}));
    runs.forEach((r, i) => scheduleWarm(`h${i}`, r, 'home'));
    const accepted = (n: number) => runs.slice(0, n);
    expect(accepted(UNKNOWN_LINK_CAP).every(Boolean)).toBe(true);
    // Untracked (a tap's own warm) and a fresh group are not capped by home's count.
    const tap = vi.fn(async () => {}); scheduleWarm('tap', tap);
    resetWarmGroup('search');
    const s = vi.fn(async () => {}); scheduleWarm('s0', s, 'search');
    return new Promise<void>((done) => setTimeout(() => {
      expect(runs.filter((r) => r.mock.calls.length).length).toBe(UNKNOWN_LINK_CAP);
      expect(tap).toHaveBeenCalled(); expect(s).toHaveBeenCalled();
      done();
    }, 0));
  });
  it('with the API present the cap does not apply', async () => {
    const runs = Array.from({ length: UNKNOWN_LINK_CAP + 3 }, () => vi.fn(async () => {}));
    runs.forEach((r, i) => scheduleWarm(`w${i}`, r, 'home'));
    await new Promise((r) => setTimeout(r, 0));
    expect(runs.filter((r) => r.mock.calls.length).length).toBe(UNKNOWN_LINK_CAP + 3);
  });
});

describe('details + ratings warmed together, in memory', () => {
  it('a warm-up fetches the title ratings the sheet will ask for', async () => {
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string) => { calls.push(String(u)); return String(u).includes('/api/movie/') ? json({ title: 'Arrival', release_date: '2016-11-11' }) : json({}); }));
    await warmDetail('movie', 5003, 329865, { images: false, ratings: true });
    await new Promise((r) => setTimeout(r, 0));
    expect(calls.some((u) => u.includes('/api/movie/329865'))).toBe(true);
    expect(calls.some((u) => u.includes('/api/plex/ratings/5003'))).toBe(true);
    expect(calls.some((u) => u.includes('/api/ratings/Arrival?year=2016'))).toBe(true);
  });
  it('the sheet hooks read ratings through the shared in-memory detailFetch; nothing is persisted', () => {
    expect(src('hooks/useMovieDetail.ts')).toMatch(/detailFetch\(plexRatingsPath\(ratingKey\)/);
    expect(src('hooks/useMovieDetail.ts')).toMatch(/detailFetch\(\s*titleRatingsPath\(/);
    expect(src('hooks/useImdbRatings.ts')).toMatch(/detailFetch\(titleRatingsPath\(title, year\)/);
    const mp = src('services/modalPrefetch.ts');
    expect(mp).not.toMatch(/localStorage|indexedDB|caches\./);
  });
});

describe('search warms after results settle', () => {
  const item = (id: number): SearchMovie => ({ id, tmdb_id: id, media_type: 'movie', title: `M${id}` } as SearchMovie);
  it('nothing before SEARCH_WARM_DEBOUNCE_MS (250); a newer result set cancels the pending one', async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string) => { calls.push(String(u)); return json({}); }));
    expect(SEARCH_WARM_DEBOUNCE_MS).toBe(250);
    const cancel1 = warmResults([item(1)]);
    vi.advanceTimersByTime(SEARCH_WARM_DEBOUNCE_MS - 1);
    expect(calls.length).toBe(0);
    cancel1();
    warmResults([item(2)]);
    vi.advanceTimersByTime(SEARCH_WARM_DEBOUNCE_MS);
    await vi.runOnlyPendingTimersAsync();
    expect(calls.some((u) => u.includes('/api/movie/2'))).toBe(true);
    expect(calls.some((u) => u.includes('/api/movie/1'))).toBe(false);
  });
  it('only results on screen are warmed when the rows are marked', async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string) => { calls.push(String(u)); return json({}); }));
    const mk = (id: number, top: number) => {
      const el = document.createElement('div'); el.setAttribute('data-warm-key', `movie::${id}`);
      el.getBoundingClientRect = () => ({ top, bottom: top + 72, left: 0, right: 300, width: 300, height: 72, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
      document.body.appendChild(el); return el;
    };
    const a = mk(3, 10), b = mk(4, window.innerHeight + 500);
    warmResults([item(3), item(4)]);
    vi.advanceTimersByTime(SEARCH_WARM_DEBOUNCE_MS);
    await vi.runOnlyPendingTimersAsync();
    expect(calls.some((u) => u.includes('/api/movie/3'))).toBe(true);
    expect(calls.some((u) => u.includes('/api/movie/4'))).toBe(false);
    a.remove(); b.remove();
  });
});

describe('every search surface opens through Home\'s sheet-open path', () => {
  it('search taps use openResult (openWhenReady), the same path as Home cards', () => {
    for (const f of ['components/search/SearchShelf.tsx', 'components/search/HomeSearchR3.tsx', 'components/search/HomeSearchPalette.tsx',
      'components/search/KioskSearch.tsx', 'components/mobile/MobileSearchOverlay.tsx']) {
      const s = src(f);
      expect(s, f).toMatch(/void openResult\(/);
      // No direct open outside openResult's callback.
      const direct = s.split('\n').filter((l) => /\bopen(Movie|Tv)\(opts\)/.test(l) && !/openResult/.test(l));
      expect(direct.filter((l) => !/=>\s*\{?\s*if \(item\.media_type|\? openTv\(opts\) : openMovie\(opts\)|else openMovie\(opts\)/.test(l)), f).toEqual([]);
    }
    expect(src('services/searchApi.ts')).toMatch(/return openWhenReady\(kind, ratingKey, tmdbId/);
    expect(src('pages/MinimalHomePage.tsx')).toMatch(/openWhenReady\(kind, ratingKey, tmdbId, el \?\? null/);
  });
});
