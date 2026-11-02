import { describe, it, expect, afterEach, vi } from 'vitest';
import { plexBackdrop, plexSheetArt, plexArtFromPath, finalBackdropUrl, PLEX_ART_W, PLEX_ART_H } from '@/utils/image';
import { keepHomePostersOnOpen, beginSheetPriority, endSheetPriority, _resetSheetPriority } from '@/loading/sheetPriority';

describe('v341o desktop Plex art is the 1280x720 transcode (Tester N1440/I1440, Designer O2)', () => {
  it('desktop never names the unsized original', () => {
    expect(PLEX_ART_W).toBe(1280); expect(PLEX_ART_H).toBe(720);
    const raw = '/api/img/plex?url=/library/metadata/92145/art/1793430969';
    expect(plexBackdrop(raw)).toMatch(/\/api\/img\/plex\?url=\/library\/metadata\/92145\/art\/1793430969&w=1280&h=720$/);
  });
  it('row prewarm, tap preload and hero name one URL (encoded card path = raw detail path)', () => {
    const a = plexArtFromPath('/library/metadata/92145/art/1793430969');
    const b = plexSheetArt('/api/img/plex?url=/library/metadata/92145/art/1793430969');
    const c = finalBackdropUrl(plexBackdrop('/api/img/plex?url=%2Flibrary%2Fmetadata%2F92145%2Fart%2F1793430969'));
    expect(a).toBe(b); expect(b).toBe(c); expect(finalBackdropUrl(a)).toBe(a);
  });
});

describe('v341q/r the hold: decoded and on-screen images never stripped, off-screen held; on-screen ones resume first', () => {
  afterEach(() => { _resetSheetPriority(); document.body.innerHTML = ''; vi.useRealTimers(); });
  const img = (src: string, rect: Partial<DOMRect>, complete = false) => {
    const card = document.createElement('div'); card.setAttribute('data-testid', 'card-1'); document.body.appendChild(card);
    const i = document.createElement('img'); i.setAttribute('src', src); card.appendChild(i);
    Object.defineProperty(i, 'complete', { value: complete, configurable: true });
    i.getBoundingClientRect = () => ({ x: 0, y: 0, width: 100, height: 150, top: 0, left: 0, bottom: 150, right: 100, toJSON: () => ({}), ...rect }) as DOMRect;
    return i;
  };
  it('a decoded poster keeps its src and priority (Close shows it at once)', () => {
    const d = img('/d.jpg', {}, true); beginSheetPriority();
    expect(d.getAttribute('src')).toBe('/d.jpg'); expect(d.hasAttribute('data-sheet-held')).toBe(false); expect(d.hasAttribute('fetchpriority')).toBe(false);
  });
  it('v341r: an on-screen poster still loading is never stripped: low while the sheet opens, its own priority back after', () => {
    const on = img('/on.jpg', {}); on.setAttribute('fetchpriority', 'auto');
    keepHomePostersOnOpen(true); beginSheetPriority(); expect(on.getAttribute('src')).toBe('/on.jpg'); expect(on.hasAttribute('data-sheet-held')).toBe(false); expect(on.getAttribute('fetchpriority')).toBe('low');
    endSheetPriority(); expect(on.getAttribute('src')).toBe('/on.jpg'); expect(on.getAttribute('fetchpriority')).toBe('auto');
  });
  it('v341r: a search open holds on-screen posters (I1440 sockets)', () => {
    const on = img('/on.jpg', {}); beginSheetPriority(); expect(on.getAttribute('src')).toBeNull(); endSheetPriority(); expect(on.getAttribute('src')).toBe('/on.jpg');
  });
  it('v341r: an off-screen one is held until the reveal', () => {
    const off = img('/off.jpg', { top: 5000, bottom: 5150 });
    beginSheetPriority(); expect(off.getAttribute('src')).toBeNull(); endSheetPriority(); expect(off.getAttribute('src')).toBe('/off.jpg');
  });
  it('off-screen ones resume after the on-screen ones load, or after 400 ms', () => {
    vi.useFakeTimers();
    const on = img('/on.jpg', {}); const off = img('/off.jpg', { top: 5000, bottom: 5150 });
    beginSheetPriority(); endSheetPriority();
    expect(on.getAttribute('src')).toBe('/on.jpg'); expect(off.getAttribute('src')).toBeNull();
    vi.advanceTimersByTime(399); expect(off.getAttribute('src')).toBeNull();
    vi.advanceTimersByTime(1); expect(off.getAttribute('src')).toBe('/off.jpg'); expect(off.hasAttribute('fetchpriority')).toBe(false);
  });
});
