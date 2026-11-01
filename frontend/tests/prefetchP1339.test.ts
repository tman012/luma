import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { _resetModalPrefetch, detailFetch, startWarmLoad, titleRatingsPath, warmDetail, RATINGS_WARMS_PER_LOAD, WARM_HEADER } from '@/services/modalPrefetch';

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
type Call = { url: string; warm: string | null };
let calls: Call[] = [];
const settle = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  _resetModalPrefetch(); calls = [];
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const h = new Headers(init?.headers);
    calls.push({ url: String(u), warm: h.get(WARM_HEADER) });
    const m = /\/api\/movie\/(\d+)/.exec(String(u));
    if (m) return json({ title: `Movie ${m[1]}`, release_date: '2020-01-01' });
    if (String(u).includes('/api/plex/movie/')) return json({ title: 'Plex Name', year: 2019, tmdb_id: 77 });
    return json({});
  }));
});
afterEach(() => { vi.unstubAllGlobals(); });
const ratingCalls = () => calls.filter((c) => c.url.includes('/api/ratings/'));

describe('ratings warm-ups per Home load', () => {
  it(`at most ${RATINGS_WARMS_PER_LOAD} flagged warm-ups per load; a new load starts over`, async () => {
    startWarmLoad();
    for (let i = 1; i <= RATINGS_WARMS_PER_LOAD + 4; i++) await warmDetail('movie', undefined, i, { images: false, ratings: true });
    await settle();
    expect(RATINGS_WARMS_PER_LOAD).toBe(12);
    expect(ratingCalls().length).toBe(12);
    expect(ratingCalls().every((c) => !!c.warm)).toBe(true);
    startWarmLoad();
    await warmDetail('movie', undefined, 100, { images: false, ratings: true });
    await settle();
    expect(ratingCalls().length).toBe(13);
  });
  it('a user open fetches ratings unflagged, past the cap', async () => {
    startWarmLoad();
    for (let i = 1; i <= RATINGS_WARMS_PER_LOAD; i++) await warmDetail('movie', undefined, i, { images: false, ratings: true });
    await warmDetail('movie', undefined, 500, { images: false, open: true });
    await settle();
    const open = ratingCalls().find((c) => c.url.includes('Movie%20500'))!;
    expect(open.warm).toBeNull();
  });
  it('without `ratings` a background warm-up fetches no ratings', async () => {
    await warmDetail('movie', undefined, 9, { images: false });
    await settle();
    expect(ratingCalls().length).toBe(0);
  });
  it('a warm-up the server skipped is asked again by the open', async () => {
    vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
      const warm = new Headers(init?.headers).get(WARM_HEADER);
      calls.push({ url: String(u), warm });
      if (String(u).includes('/api/movie/')) return json({ title: 'Skip', release_date: '2020-01-01' });
      if (String(u).includes('/api/ratings/')) return warm ? json({ error: 'Warm-up skipped' }, 429) : json({ imdb: { score: 7 } });
      return json({});
    }));
    startWarmLoad();
    await warmDetail('movie', undefined, 1, { images: false, ratings: true });
    await settle(); await settle();
    const r = await detailFetch(titleRatingsPath('Skip', 2020));
    expect(r.status).toBe(200);
    expect(ratingCalls().length).toBe(2);
  });
  it('owned titles warm ratings by the Plex title and year (the key the sheet reads)', async () => {
    startWarmLoad();
    await warmDetail('movie', 5003, 77, { images: false, ratings: true });
    await settle(); await settle();
    expect(ratingCalls().map((c) => c.url)).toEqual([expect.stringContaining('/api/ratings/Plex%20Name?year=2019')]);
  });
});

describe('where ratings warm', () => {
  it('Home: first rail and on-screen cards only; each Home load starts a new count', () => {
    const r = src('components/home/Rails.tsx');
    expect(r).toMatch(/warmCard\(card, warmsRatings\(el\)\)/);
    expect(r).toMatch(/useEffect\(\(\) => \{ startWarmLoad\(\); \}, \[\]\)/);
  });
  it('Kiosk results and phone credit rows pass the tapped element', () => {
    const k = src('components/search/KioskSearch.tsx');
    expect(k).not.toMatch(/handleResultClick\((item|entry\.item)\)/);
    expect((k.match(/\}, el\)/g) ?? []).length).toBe(3);
    expect(src('components/mobile/MobileSearchOverlay.tsx')).toMatch(/onClick=\{\(el\) => handleCreditClick\(credit, el\)\}/);
  });
});

describe('search ratings warm-ups', () => {
  it('at most SEARCH_RATINGS_WARMS (4) per query, flagged, counted against the same load cap', async () => {
    const { SEARCH_RATINGS_WARMS, startSearchWarms } = await import('@/services/modalPrefetch');
    startWarmLoad();
    for (let i = 1; i <= 6; i++) await warmDetail('movie', undefined, i, { images: false, ratings: true, search: true });
    await settle();
    expect(SEARCH_RATINGS_WARMS).toBe(4);
    expect(ratingCalls().length).toBe(4);
    expect(ratingCalls().every((c) => !!c.warm)).toBe(true);
    // A new query gets 4 more, and the load cap still holds across Home and search.
    startSearchWarms();
    for (let i = 11; i <= 16; i++) await warmDetail('movie', undefined, i, { images: false, ratings: true, search: true });
    for (let i = 21; i <= 30; i++) await warmDetail('movie', undefined, i, { images: false, ratings: true });
    await settle();
    expect(ratingCalls().length).toBe(RATINGS_WARMS_PER_LOAD);
  });
  it('the search warm-up asks for ratings only through the capped, flagged path', () => {
    const s = src('services/searchApi.ts');
    expect(s).toMatch(/startSearchWarms\(\);/);
    expect(s).toMatch(/warmResult\(item, \{ images: full, ratings: full, search: true \}\)/);
  });
});
