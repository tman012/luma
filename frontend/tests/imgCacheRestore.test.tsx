import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAllCaches, noteSignedInUser } from '@/contexts/AuthContext';
import { detailFetch, scheduleWarm, STARTED_MAX, WARM_CONCURRENCY, _resetModalPrefetch } from '@/services/modalPrefetch';

const json = () => new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  _resetModalPrefetch();
  localStorage.clear();
  fetchMock = vi.fn(async () => json());
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('caches', { keys: async () => [], delete: async () => true });
});
afterEach(() => { vi.unstubAllGlobals(); _resetModalPrefetch(); });

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('in-memory prefetch is per account', () => {
  it('sign-out forgets prefetched popup details', async () => {
    await (await detailFetch('/api/movie/1')).text();
    await (await detailFetch('/api/movie/1')).text();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    clearAllCaches();
    await (await detailFetch('/api/movie/1')).text();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('an account switch forgets them too; the same account keeps them', async () => {
    noteSignedInUser(1);
    await (await detailFetch('/api/movie/2')).text();
    noteSignedInUser(1);
    await (await detailFetch('/api/movie/2')).text();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    noteSignedInUser(2);
    await (await detailFetch('/api/movie/2')).text();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('warm-up bookkeeping', () => {
  it('remembers at most STARTED_MAX warm-ups, forgetting the oldest', async () => {
    expect(STARTED_MAX).toBe(500);
    for (let i = 0; i <= STARTED_MAX; i++) scheduleWarm(`k${i}`, async () => {});
    await flush();
    const again = vi.fn(async () => {});
    scheduleWarm(`k${STARTED_MAX}`, again); // newest: still remembered
    await flush();
    expect(again).not.toHaveBeenCalled();
    scheduleWarm('k0', again); // oldest: forgotten past the cap
    await flush();
    expect(again).toHaveBeenCalledTimes(1);
  });

  it('jobs from before a reset never free slots of jobs after it', async () => {
    const old: (() => void)[] = [];
    for (let i = 0; i < WARM_CONCURRENCY; i++) {
      scheduleWarm(`old${i}`, () => new Promise<void>((res) => { old.push(res); }));
    }
    _resetModalPrefetch();
    let running = 0;
    for (let i = 0; i < WARM_CONCURRENCY + 1; i++) {
      scheduleWarm(`new${i}`, () => { running += 1; return new Promise<void>(() => {}); });
    }
    expect(running).toBe(WARM_CONCURRENCY);
    old.forEach((res) => res());
    await flush(); await flush();
    expect(running).toBe(WARM_CONCURRENCY);
  });
});
