import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { loadImage, isDecoded, _resetImageLoader } from '@/loading/imageLoader';
import { beginSheetPriority, endSheetPriority, _resetSheetPriority } from '@/loading/sheetPriority';
import { ownedKey, resolveOwnership } from '@/services/ownership';

// Reviewer B1 repro: a fake Image whose decode() rejects when its src is removed (a browser's abort).
class FakeImage {
  static all: FakeImage[] = [];
  decoding = ''; fetchPriority = ''; onload: (() => void) | null = null; onerror: (() => void) | null = null;
  url = ''; private res?: () => void; private rej?: () => void;
  set src(v: string) { this.url = v; FakeImage.all.push(this); }
  get src() { return this.url; }
  removeAttribute(a: string) { if (a === 'src') { this.url = ''; this.rej?.(); } }
  decode() { return new Promise<void>((res, rej) => { this.res = res; this.rej = rej; }); }
  finish() { if (this.url) this.res?.(); }
}
const flush = () => new Promise((r) => setTimeout(r, 0));
async function finishAll() { for (let k = 0; k < 6; k++) { for (const i of FakeImage.all) i.finish(); await flush(); } }

describe('v341j: Reviewer B1, the background art queue survives an open', () => {
  const orig = globalThis.Image;
  beforeEach(() => { _resetImageLoader(); _resetSheetPriority(); FakeImage.all = []; (globalThis as { Image: unknown }).Image = FakeImage; });
  afterEach(() => { (globalThis as { Image: unknown }).Image = orig; });

  it('aborted /a and /b requeue and complete; /c, /d and /e start and finish; nothing is failed by an abort', async () => {
    const p = ['/a', '/b', '/c', '/d'].map((u) => loadImage(u, 'low'));
    expect(FakeImage.all.map((i) => i.url)).toEqual(['/a', '/b']);
    beginSheetPriority();
    await flush();
    // The aborts' rejected decodes are no result: nothing failed, nothing settled.
    expect(['/a', '/b'].some((u) => isDecoded(u))).toBe(false);
    let settled = 0; p.forEach((x) => x.then(() => { settled++; }));
    await flush();
    expect(settled).toBe(0);
    endSheetPriority();
    await finishAll();
    await Promise.all(p);
    for (const u of ['/a', '/b', '/c', '/d']) expect(isDecoded(u)).toBe(true);
    const e = loadImage('/e', 'low');
    expect(FakeImage.all.some((i) => i.url === '/e')).toBe(true);
    await finishAll(); await e;
    expect(isDecoded('/e')).toBe(true);
  });

  it('5 opens in a session (each during 2 in-flight warm-ups): the queue never wedges', async () => {
    for (let n = 0; n < 5; n++) {
      const ps = [0, 1, 2].map((i) => loadImage(`/s${n}-${i}`, 'low'));
      beginSheetPriority();
      const tap = loadImage(`/tap${n}`, 'high');
      await flush(); for (const i of FakeImage.all) if (i.url === `/tap${n}`) i.finish(); await tap;
      endSheetPriority();
      await finishAll();
      await Promise.all(ps);
      for (let i = 0; i < 3; i++) expect(isDecoded(`/s${n}-${i}`)).toBe(true);
      expect(isDecoded(`/tap${n}`)).toBe(true);
      // Warm stays warm: a warmed image asked for again is served from memory (no new download).
      const before = FakeImage.all.length; await loadImage(`/s${n}-0`, 'high'); expect(FakeImage.all.length).toBe(before);
    }
  });

  it('a tapped warm-up restarts at high once (the low one dropped), and settles from the live image', async () => {
    const a = loadImage('/x', 'low');
    void loadImage('/x', 'high');
    expect(FakeImage.all.filter((i) => i.url === '/x').length).toBe(1);
    await finishAll(); await a;
    expect(isDecoded('/x')).toBe(true);
  });
});

describe('v341j: ownedKey honors the TTL and the TMDb id', () => {
  it('matches only a fresh entry with the same TMDb id', async () => {
    vi.useFakeTimers({ now: 1_000_000, toFake: ['Date'] });
    await resolveOwnership([{ title: 'Tulsa King', year: 2022, tmdbId: 153312, ratingKey: 39580, mediaType: 'tv' }]);
    expect(ownedKey({ title: 'Tulsa King', year: 2022, mediaType: 'tv', tmdbId: 153312 })).toBe(39580);
    expect(ownedKey({ title: 'Tulsa King', year: 2022, mediaType: 'tv', tmdbId: 999 })).toBeUndefined();
    vi.setSystemTime(1_000_000 + 61_000);
    expect(ownedKey({ title: 'Tulsa King', year: 2022, mediaType: 'tv', tmdbId: 153312 })).toBeUndefined();
    vi.useRealTimers();
  });
});

describe('v341j: no slot leaks when the LRU evicts a queued or in-flight load', () => {
  const orig = globalThis.Image;
  beforeEach(() => { _resetImageLoader(); _resetSheetPriority(); FakeImage.all = []; (globalThis as { Image: unknown }).Image = FakeImage; });
  afterEach(() => { (globalThis as { Image: unknown }).Image = orig; });
  it('an evicted URL asked for again still frees its slot; the queue drains', async () => {
    const ps = [loadImage('/a', 'low'), loadImage('/b', 'low')];
    for (let i = 0; i < 160; i++) ps.push(loadImage(`/q${i}`, 'low'));   // evicts /a and /b (LRU 150)
    ps.push(loadImage('/a', 'low'));                                       // a new entry for /a
    beginSheetPriority(); endSheetPriority();
    await finishAll();
    for (let k = 0; k < 200 && FakeImage.all.some((i) => i.url); k++) await finishAll();
    await Promise.all(ps);
    const z = loadImage('/z', 'low');
    expect(FakeImage.all.some((i) => i.url === '/z')).toBe(true);
    await finishAll(); await z;
    expect(isDecoded('/z')).toBe(true);
  });
});
