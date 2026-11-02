import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { loadImage, _resetImageLoader, LOW_CAP } from '@/loading/imageLoader';
import { beginSheetPriority, endSheetPriority, _resetSheetPriority } from '@/loading/sheetPriority';
import { cardArt } from '@/components/home/Rails';

const srcs: string[] = [];
const orig = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src')!;
Object.defineProperty(HTMLImageElement.prototype, 'src', {
  configurable: true,
  get() { return orig.get!.call(this); },
  set(v: string) { srcs.push(v); orig.set!.call(this, v); },
});

describe('v341i: art consistency and background art queue', () => {
  beforeEach(() => { _resetImageLoader(); _resetSheetPriority(); srcs.length = 0; });

  it(`background art downloads ${LOW_CAP} at a time; the tap's art starts at once`, () => {
    for (let i = 0; i < 6; i++) void loadImage(`/bg${i}.jpg`, 'low');
    expect(srcs).toEqual(['/bg0.jpg', '/bg1.jpg']);
    void loadImage('/tap.jpg', 'high');
    expect(srcs).toContain('/tap.jpg');
  });

  it('an open holds queued background art and drops the in-flight ones until its reveal', () => {
    for (let i = 0; i < 4; i++) void loadImage(`/bg${i}.jpg`, 'low');
    srcs.length = 0;
    beginSheetPriority();
    void loadImage('/bg9.jpg', 'low');
    void loadImage('/tap.jpg', 'high');
    expect(srcs).toEqual(['/tap.jpg']);
    endSheetPriority();
    expect(srcs.slice(1)).toEqual(['/bg0.jpg', '/bg1.jpg']);
  });

  it('a queued warm-up of the tapped art starts at high priority (never refetched)', () => {
    for (let i = 0; i < 3; i++) void loadImage(`/bg${i}.jpg`, 'low');
    void loadImage('/bg2.jpg', 'high');
    expect(srcs.filter((s) => s.endsWith('/bg2.jpg')).length).toBe(1);
  });

  it('F1: cardArt takes only the proxy art pattern, URL-encoded', () => {
    expect(cardArt({ art_path: '/library/metadata/39580/art/1699' } as never)[0]).toContain('/library/metadata/39580/art/1699');
    expect(cardArt({ art_path: '/library/metadata/1/thumb' } as never)).toEqual([]);
    expect(cardArt({ art_path: '/library/metadata/1/art?x=1&y' } as never)).toEqual([]);
    expect(cardArt({ art_path: 'http://evil/library/metadata/1/art' } as never)).toEqual([]);
    expect(readFileSync('src/components/home/Rails.tsx', 'utf8')).toMatch(/encodeURIComponent\(c\.art_path\)/);
  });

  it('owned titles always show Plex art; TMDb only when Plex has none', () => {
    const m = readFileSync('src/hooks/useMovieDetail.ts', 'utf8');
    expect(m).toMatch(/if \(tmdbData\.backdrop_path && !plexArt\)/);
    const t = readFileSync('src/hooks/useTvDetail.ts', 'utf8');
    expect(t).toMatch(/if \(plexTmdbId && !plexArtNow\)/);
  });
});
