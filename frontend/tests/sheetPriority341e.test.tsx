import { describe, it, expect, afterEach } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { beginSheetPriority, endSheetPriority, sheetOpening, afterSheetPriority, _resetSheetPriority } from '@/loading/sheetPriority';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
afterEach(() => { _resetSheetPriority(); document.body.innerHTML = ''; });

describe('v341e: the opening sheet\'s art owns the network', () => {
  it('offscreen page images pause and resume after the reveal; onscreen ones drop to low priority', () => {
    document.body.innerHTML = '<img id="on" src="/a.jpg"><img id="off" src="/b.jpg" srcset="/b2.jpg 2x">';
    const on = document.getElementById('on') as HTMLImageElement, off = document.getElementById('off') as HTMLImageElement;
    Object.defineProperty(on, 'complete', { value: false }); Object.defineProperty(off, 'complete', { value: false });
    on.getBoundingClientRect = () => ({ top: 10, bottom: 100, left: 0, right: 100, width: 100, height: 90 }) as DOMRect;
    off.getBoundingClientRect = () => ({ top: 2000, bottom: 2100, left: 0, right: 100, width: 100, height: 100 }) as DOMRect;
    beginSheetPriority();
    expect(sheetOpening()).toBe(true);
    expect(on.getAttribute('fetchpriority')).toBe('low');
    expect(off.getAttribute('src')).toBeNull();
    let ran = false; afterSheetPriority(() => { ran = true; });
    endSheetPriority();
    expect(off.getAttribute('src')).toBe('/b.jpg'); expect(off.getAttribute('srcset')).toBe('/b2.jpg 2x');
    expect(ran).toBe(true); expect(sheetOpening()).toBe(false);
  });
  it('warm-ups wait while a sheet opens; the tap starts the window, the one reveal ends it; the sheet decodes at high priority', () => {
    const m = src('services/modalPrefetch.ts');
    expect(m).toMatch(/if \(sheetOpening\(\)\) \{ afterSheetPriority\(pump\); return; \}/);
    expect(m).toMatch(/tapAt = now\(\);\n  beginSheetPriority\(\);/);
    expect(src('hooks/useOneReveal.ts')).toMatch(/if \(ready\) endSheetPriority\(\)/);
    expect(src('hooks/useSheetBackdrop.ts')).toMatch(/fetchPriority = 'high'/);
  });
  it('desktop: the backdrop img mounts hidden once its URL is known (painted by the reveal), at high priority', () => {
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      const s = src(`components/media/${k}/ExpandedLayout.tsx`);
      expect(s, k).toMatch(/const artEl = sheetArt \?\? \(sheetHero\.state === 'hold' \? artCandidate : null\);/);
      expect(s, k).toMatch(/key=\{`\$\{String\(props\.openKey\)\}\|\$\{artEl\}`\}[\s\S]{0,400}alt="" fetchPriority="high"/);
    }
  });
});
describe('v341e: desktop reveal waits for its backdrop img element (warm opens: no buttons before the art)', () => {
  it('movie and TV expanded', () => {
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      const s = src(`components/media/${k}/ExpandedLayout.tsx`);
      expect(s, k).toMatch(/sheetHero\.state !== 'hold' && artInFrame, /);
      expect(s, k).toMatch(/onLoad=\{\(e\) => \{[^\n]*setArtPainted\(artEl\)\); \}\} onError=\{\(\) => setArtPainted\(artEl\)\}/);
      expect(s, k).toMatch(/!!artPaintedAt && artPaintedAt\.key === props\.openKey && artPaintedAt\.url === sheetArt/);
    }
  });
});
describe('v341e: an open frees the connections and starts its Plex art early', () => {
  it('in-flight detail requests for other titles are aborted at the tap; the Plex art preloads when Plex names it', () => {
    const m = src('services/modalPrefetch.ts');
    expect(m).toMatch(/beginSheetPriority\(\);\n  dropOtherWarms\(\[ratingKey, tmdbId\], card \? tapTitle\(card\) : undefined\);/);
    expect(m).toMatch(/inflight\.delete\(url\); ac\.abort\(\);/);
    expect(m).toMatch(/const earlyArt = \(u: string \| null\) => \{ if \(opts\.open && images && u\) void preloadImage\(finalBackdropUrl\(u\), 'high'\); \};/);
  });
  it('the art deadline latches the last backdrop that decoded for the open when the newer pick is still decoding', () => {
    const s = src('hooks/useSheetBackdrop.ts');
    expect(s).toMatch(/const fallback = cur\.artCapped && present && !artIn \? good\.current\.url : null;/);
  });
});
describe('v341f: desktop content and backdrop start their fade on one trigger', () => {
  it('the content runs the backdrop\'s HERO_ANIM on its own layer (one start time)', () => {
    expect(src('hooks/useSheetBackdrop.ts')).toMatch(/export const CONTENT_WITH_HERO = \{ transition: 'none', animation: HERO_ANIM, willChange: 'opacity' \} as const;/);
  });
  it('the content is hidden on data ready and fades from 0 at the reveal (no full-opacity frame via loading)', () => {
    for (const [k, v] of [['MovieDetailModal', 'liveDetail'], ['TvDetailModal', 'liveShow']]) {
      const s = src(`components/media/${k}/ExpandedLayout.tsx`);
      expect(s, k).toContain(`...((loading && !${v}) || error ? revealStyle('text') : reveal.revealed ? { opacity: 1, ...CONTENT_WITH_HERO } : { opacity: 0, transition: 'none' }),`);
      expect(s, k).toMatch(/im\.decode\(\) : Promise\.resolve\(\)\)\.catch\(\(\) => \{\}\)\.then\(\(\) => setArtPainted\(artEl\)\)/);
    }
  });
});
describe('v341f: Reviewer F1/F2/F4', () => {
  it('F1: an aborted or failed request only forgets its own cache entry; F4: the opened title\'s ratings warm stays', () => {
    const m = src('services/modalPrefetch.ts');
    expect(m).toMatch(/const e = cache\.get\(url\); if \(e && e\.ac === ac\) cache\.delete\(url\);/);
    expect(m).toMatch(/const forget = \(\) => \{ if \(cache\.get\(url\) === entry\) cache\.delete\(url\); \};/);
    expect(m).toMatch(/\(ratingsOfTitle && url\.startsWith\(ratingsOfTitle\)\)\) continue;/);
  });
  it('F2: the desktop wait for the backdrop element is capped at the art deadline', () => {
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      expect(src(`components/media/${k}/ExpandedLayout.tsx`), k).toMatch(/const artInFrame = !sheetArt \|\| artWaitCapped \|\| /);
    }
    expect(src('hooks/useSheetBackdrop.ts')).toMatch(/capLeft\(BACKDROP_ART_DEADLINE_MS\)\);\n    return \(\) => window\.clearTimeout\(t\);\n  \}, \[openKey\]\);\n  return k === openKey;/);
  });
});
describe('v341g: Tester PREP B1/B2', () => {
  it('B1: the open keeps the requests of the chain warming its own title; background chains stop while a sheet opens', () => {
    const m = src('services/modalPrefetch.ts');
    expect(m).toMatch(/if \(\(owners\.get\(url\) \?\? \[\]\)\.some\(\(o\) => ids\.some\(\(x\) => !!x && o\.has\(x\)\)\)\) continue;/);
    expect(m).toMatch(/const yieldToSheet = \(\) => !opts\.open && sheetOpening\(\);/);
    expect(m).toMatch(/if \(yieldToSheet\(\)\) return;\n  if \(!images\) return;/);
  });
  it('B1: a Plex-keyed movie whose TMDb missed the brief wait keeps the art deadline waiting (backdropPending)', () => {
    expect(src('hooks/useMovieDetail.ts')).toMatch(/if \(!tmdbData && parsed\.tmdbId && !parsed\.backdropUrl\) parsed\.backdropPending = true;/);
    for (const k of ['CompactLayout', 'ExpandedLayout']) {
      expect(src(`components/media/MovieDetailModal/${k}.tsx`), k).toMatch(/!!liveDetail && !liveDetail\.backdropPending, liveDetail\?\.clearLogoUrl/);
    }
  });
  it('B2: a show opened by its Plex key is owned whatever TMDb does', () => {
    const t = src('hooks/useTvDetail.ts');
    expect(t.match(/setShowOwned\(true\);/g)?.length).toBeGreaterThanOrEqual(3);
    expect(t).toMatch(/setShowOwned\(result\.is_owned \|\| !!ratingKey\);/);
  });
});
