import { describe, it, expect, beforeEach } from 'vitest';
import { beginSheetPriority, endSheetPriority, _resetSheetPriority } from '@/loading/sheetPriority';
import { resultArt, resultIds } from '@/services/searchApi';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('v341k: a tap holds every page image until the reveal (rail posters too, and ones that start after the tap)', () => {
  beforeEach(() => { _resetSheetPriority(); document.body.innerHTML = ''; });
  it('holds on-screen and later-mounted posters, never the sheet, and restores them at the reveal', async () => {
    document.body.innerHTML = '<img id="a" src="/p1.jpg" srcset="/p1.jpg 1x"><div data-modal="TvDetailModal"><img id="s" src="/art.jpg"></div>';
    beginSheetPriority();
    const a = document.getElementById('a')!;
    expect(a.getAttribute('src')).toBeNull();
    expect(document.getElementById('s')!.getAttribute('src')).toBe('/art.jpg');
    const late = document.createElement('img'); late.setAttribute('src', '/p2.jpg'); document.body.appendChild(late);
    await flush();
    expect(late.getAttribute('src')).toBeNull();                       // started after the tap: held too
    endSheetPriority();
    expect(a.getAttribute('src')).toBe('/p1.jpg');
    expect(a.getAttribute('srcset')).toBe('/p1.jpg 1x');
    expect(late.getAttribute('src')).toBe('/p2.jpg');
    const after = document.createElement('img'); after.setAttribute('src', '/p3.jpg'); document.body.appendChild(after);
    await flush();
    expect(after.getAttribute('src')).toBe('/p3.jpg');                 // after the reveal nothing is held
  });
});

describe('v341k: an owned search row opens like its Home card (both ids, its Plex art from the tap)', () => {
  it('uses the server art_path, never the TMDb backdrop', () => {
    const row = { id: 92145, rating_key: 92145, tmdb_id: 10061, isOwned: true, media_type: 'movie', title: 'Escape from L.A.',
      art_path: '/library/metadata/92145/art/1793430969', backdrop_path: '/tmdb.jpg' };
    expect(resultIds(row as never)).toEqual({ kind: 'movie', ratingKey: 92145, tmdbId: 10061 });
    const art = resultArt(row as never);
    expect(art).toHaveLength(1);
    expect(art[0]).toContain('/library/metadata/92145/art/1793430969');
    expect(art[0]).not.toContain('tmdb');
    expect(resultArt({ ...row, art_path: '/library/metadata/1/thumb', backdrop_url: null } as never)[0]).toContain('tmdb');
  });
});

describe('v341l: the hold covers only the page under the sheet; nested opens and re-renders (Reviewer F1/F2)', () => {
  beforeEach(() => { _resetSheetPriority(); document.body.innerHTML = ''; });
  it('never holds a dialog or another sheet (search palette, person sheet)', () => {
    document.body.innerHTML = '<div role="dialog"><img id="p" src="/row.jpg"></div><div data-modal="PersonDetailModal"><img id="q" src="/face.jpg"></div>';
    beginSheetPriority();
    expect(document.getElementById('p')!.getAttribute('src')).toBe('/row.jpg');
    expect(document.getElementById('q')!.getAttribute('src')).toBe('/face.jpg');
    endSheetPriority();
  });
  it('a second open inside the hold keeps one record per image and restores it once', async () => {
    document.body.innerHTML = '<img id="a" src="/p1.jpg">';
    beginSheetPriority(); beginSheetPriority();
    expect(document.getElementById('a')!.getAttribute('src')).toBeNull();
    endSheetPriority();
    expect(document.getElementById('a')!.getAttribute('src')).toBe('/p1.jpg');
  });
  it('a src a re-render sets while held is held too, and is the one restored', async () => {
    document.body.innerHTML = '<img id="a" src="/old.jpg">';
    beginSheetPriority();
    const a = document.getElementById('a')!;
    a.setAttribute('src', '/new.jpg');
    await flush();
    expect(a.getAttribute('src')).toBeNull();
    endSheetPriority();
    expect(a.getAttribute('src')).toBe('/new.jpg');
  });
});

describe('v341l headroom: an open cancels off-page poster decodes still downloading, restarted after the reveal', () => {
  beforeEach(() => { _resetSheetPriority(); });
  it('drops the src of an in-flight decode at the tap and loads it again after endSheetPriority', async () => {
    const made: HTMLImageElement[] = [];
    const Real = globalThis.Image;
    class FakeImage {
      decoding = ''; complete = false; private _src: string | null = null; private rej: ((e: unknown) => void) | null = null;
      constructor() { made.push(this as unknown as HTMLImageElement); }
      set src(v: string) { this._src = v; }
      get src() { return this._src ?? ''; }
      removeAttribute(n: string) { if (n === 'src') { this._src = null; this.rej?.(new Error('aborted')); } }
      decode() { return new Promise<void>((_res, rej) => { this.rej = rej; }); }
    }
    (globalThis as { Image: unknown }).Image = FakeImage;
    try {
      const { pageDecode } = await import('@/loading/sheetPriority');
      void pageDecode('/poster.jpg');
      expect(made.length).toBe(1);
      beginSheetPriority();
      await flush();
      expect(made[0]!.src).toBe('');
      expect(made.length).toBe(1);          // nothing starts while the sheet opens
      endSheetPriority();
      expect(made.length).toBe(2);
      expect(made[1]!.src).toBe('/poster.jpg');
    } finally { (globalThis as { Image: unknown }).Image = Real; }
  });
});
