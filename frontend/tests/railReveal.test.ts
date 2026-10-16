import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  REVEAL_CAP_MS,
  runRevealGate,
  revealedRails,
  skeletonRails,
  loadSnapshot,
  saveSnapshot,
  loadLayout,
  saveLayout,
  withDeadline,
  type StorageLike,
} from '../src/features/rails/railReveal';

type Id = 'a' | 'b' | 'c';
const defs = [
  { id: 'a' as const, title: 'A', conditional: false },
  { id: 'b' as const, title: 'B', conditional: true },
  { id: 'c' as const, title: 'C', conditional: false },
];

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

class MemStorage implements StorageLike {
  m = new Map<string, string>();
  getItem(k: string) { return this.m.get(k) ?? null; }
  setItem(k: string, v: string) { this.m.set(k, v); }
  removeItem(k: string) { this.m.delete(k); }
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('runRevealGate', () => {
  it('reveals only once every rail has succeeded', async () => {
    const d = { a: deferred<number[]>(), b: deferred<number[]>(), c: deferred<number[]>() };
    const gate = runRevealGate<Id, number>({ ids: ['a', 'b', 'c'], fetch: (id) => d[id].promise });
    let done = false;
    void gate.done.then(() => { done = true; });
    d.a.resolve([1]);
    d.c.resolve([3]);
    await vi.advanceTimersByTimeAsync(5000);
    expect(done).toBe(false); // fast rails do not show on their own
    d.b.resolve([2]);
    const r = await gate.done;
    expect([...r.ok.keys()].sort()).toEqual(['a', 'b', 'c']);
    expect(r.failed).toEqual([]);
    expect(r.timedOut).toBe(false);
  });

  it('retries a failed rail in the background before revealing', async () => {
    let calls = 0;
    const gate = runRevealGate<Id, number>({
      ids: ['a', 'b'],
      fetch: async (id) => {
        if (id === 'b' && ++calls < 3) throw new Error('down');
        return [1];
      },
      retryDelaysMs: [100, 200],
    });
    await vi.advanceTimersByTimeAsync(1000);
    const r = await gate.done;
    expect(calls).toBe(3);
    expect(r.ok.get('b')).toEqual([1]);
    expect(r.timedOut).toBe(false);
  });

  it('reveals what succeeded at the cap and leaves failed rails out', async () => {
    const gate = runRevealGate<Id, number>({
      ids: ['a', 'b', 'c'],
      fetch: async (id) => { if (id === 'b') throw new Error('down'); return [1]; },
    });
    let done = false;
    void gate.done.then(() => { done = true; });
    await vi.advanceTimersByTimeAsync(REVEAL_CAP_MS - 1);
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    const r = await gate.done;
    expect(r.timedOut).toBe(true);
    expect(r.failed).toEqual(['b']);
    expect(revealedRails(defs, r.ok, true).map((x) => x.id)).toEqual(['a', 'c']);
  });

  it('caps a hung rail at about 15 seconds', async () => {
    const hung = deferred<number[]>();
    const gate = runRevealGate<Id, number>({
      ids: ['a', 'c'],
      fetch: (id) => (id === 'c' ? hung.promise : Promise.resolve([1])),
    });
    await vi.advanceTimersByTimeAsync(15_000);
    const r = await gate.done;
    expect(r.failed).toEqual(['c']);
  });

  it('never inserts a rail that succeeds after the reveal', async () => {
    const late = deferred<number[]>();
    const gate = runRevealGate<Id, number>({
      ids: ['a', 'b'],
      fetch: (id) => (id === 'b' ? late.promise : Promise.resolve([1])),
      capMs: 1000,
    });
    await vi.advanceTimersByTimeAsync(1000);
    const r = await gate.done;
    late.resolve([2]);
    await vi.advanceTimersByTimeAsync(10);
    expect(r.ok.has('b')).toBe(false);
    expect(revealedRails(defs, r.ok, true).map((x) => x.id)).toEqual(['a']);
  });

  it('stops retrying after the reveal', async () => {
    let calls = 0;
    const gate = runRevealGate<Id, number>({
      ids: ['a'],
      fetch: async () => { calls++; throw new Error('down'); },
      capMs: 1000,
      retryDelaysMs: [100],
    });
    await vi.advanceTimersByTimeAsync(1000);
    await gate.done;
    const after = calls;
    await vi.advanceTimersByTimeAsync(5000);
    expect(calls).toBe(after);
  });

  it('cancel stops everything without resolving', async () => {
    let calls = 0;
    const gate = runRevealGate<Id, number>({
      ids: ['a'],
      fetch: async () => { calls++; throw new Error('down'); },
      retryDelaysMs: [100],
    });
    let done = false;
    void gate.done.then(() => { done = true; });
    await vi.advanceTimersByTimeAsync(50);
    gate.cancel();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(done).toBe(false);
    expect(calls).toBe(1);
  });

  it('treats a synchronous fetch throw as a failure to retry', async () => {
    let n = 0;
    const gate = runRevealGate<Id, number>({
      ids: ['a'],
      fetch: () => { if (n++ === 0) throw new Error('sync'); return Promise.resolve([1]); },
      retryDelaysMs: [10],
    });
    await vi.advanceTimersByTimeAsync(20);
    expect((await gate.done).ok.get('a')).toEqual([1]);
  });
});

describe('rail state shaping', () => {
  it('reveals every rail on the same state with one shared fade flag', () => {
    const ok = new Map<Id, number[] | null>([['a', [1]], ['b', [2]], ['c', [3]]]);
    const rails = revealedRails(defs, ok, true);
    expect(rails.map((r) => r.id)).toEqual(['a', 'b', 'c']);
    expect(rails.every((r) => r.fadeIn && !r.isLoading)).toBe(true);
  });

  it('hides disabled and empty conditional rails', () => {
    const ok = new Map<Id, number[] | null>([['a', []], ['b', []], ['c', null]]);
    expect(revealedRails(defs, ok, false).map((r) => r.id)).toEqual(['a']);
  });

  it('skeletons cover always-on rails plus conditional rails seen last time', () => {
    expect(skeletonRails(defs, null).map((r) => r.id)).toEqual(['a', 'c']);
    expect(skeletonRails(defs, new Set<Id>(['b'])).map((r) => r.id)).toEqual(['a', 'b', 'c']);
    expect(skeletonRails(defs, null).every((r) => r.isLoading)).toBe(true);
  });
});

describe('cached snapshot', () => {
  it('reveals instantly from a fresh snapshot with no skeletons or fade', () => {
    const s = new MemStorage();
    saveSnapshot(s, 'k', revealedRails(defs, new Map<Id, number[]>([['a', [1]], ['c', [3]]]), true), 1000);
    const snap = loadSnapshot<Id, number>(s, 'k', 60_000, 2000);
    expect(snap).not.toBeNull();
    const rails = revealedRails(defs, snap!, false);
    expect(rails.map((r) => r.id)).toEqual(['a', 'c']);
    expect(rails.some((r) => r.isLoading || r.fadeIn)).toBe(false);
  });

  it('drops an expired snapshot', () => {
    const s = new MemStorage();
    saveSnapshot(s, 'k', revealedRails(defs, new Map<Id, number[]>([['a', [1]]]), true), 0);
    expect(loadSnapshot(s, 'k', 1000, 5000)).toBeNull();
    expect(s.getItem('k')).toBeNull();
  });

  it('ignores corrupt or missing storage', () => {
    const s = new MemStorage();
    s.setItem('k', '{nope');
    expect(loadSnapshot(s, 'k', 1000)).toBeNull();
    expect(loadSnapshot(null, 'k', 1000)).toBeNull();
  });

  it('remembers the revealed layout for the next skeleton pass', () => {
    const s = new MemStorage();
    saveLayout(s, 'l', [{ id: 'a' }, { id: 'b' }]);
    expect([...loadLayout<Id>(s, 'l')!]).toEqual(['a', 'b']);
  });
});

describe('withDeadline', () => {
  it('falls back when enrichment is slow', async () => {
    const slow = deferred<string>();
    const p = withDeadline(slow.promise, 100, 'fallback');
    await vi.advanceTimersByTimeAsync(100);
    expect(await p).toBe('fallback');
  });
  it('falls back when enrichment fails', async () => {
    expect(await withDeadline(Promise.reject(new Error('x')), 100, 'fb')).toBe('fb');
  });
});
