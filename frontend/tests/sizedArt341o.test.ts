import { describe, it, expect, afterEach } from 'vitest';
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

describe('v341o the hold leaves on-screen page images loading (R1/(y))', () => {
  afterEach(() => { _resetSheetPriority(); document.body.innerHTML = ''; });
  const img = (src: string, rect: Partial<DOMRect>) => {
    const card = document.createElement('div'); card.setAttribute('data-testid', 'card-1'); document.body.appendChild(card);
    const i = document.createElement('img'); i.setAttribute('src', src); card.appendChild(i);
    i.getBoundingClientRect = () => ({ x: 0, y: 0, width: 100, height: 150, top: 0, left: 0, bottom: 150, right: 100, toJSON: () => ({}), ...rect }) as DOMRect;
    return i;
  };
  it('a search open holds on-screen Home posters too (I390)', () => {
    const on = img('/on.jpg', {}); beginSheetPriority(); expect(on.getAttribute('src')).toBeNull(); endSheetPriority(); expect(on.getAttribute('src')).toBe('/on.jpg');
  });
  it('a search-grid poster on screen is still held (I1440)', () => {
    const s = document.createElement('div'); s.setAttribute('data-testid', 'r3-card'); document.body.appendChild(s);
    const i = document.createElement('img'); i.setAttribute('src', '/s.jpg'); s.appendChild(i);
    i.getBoundingClientRect = () => ({ x: 0, y: 0, width: 100, height: 150, top: 0, left: 0, bottom: 150, right: 100, toJSON: () => ({}) }) as DOMRect;
    (keepHomePostersOnOpen(true), beginSheetPriority()); expect(i.getAttribute('src')).toBeNull(); endSheetPriority(); expect(i.getAttribute('src')).toBe('/s.jpg');
  });
  it('on screen: untouched; off screen: held and restored at the end', () => {
    const on = img('/on.jpg', {}); const off = img('/off.jpg', { top: 5000, bottom: 5150 });
    (keepHomePostersOnOpen(true), beginSheetPriority());
    expect(on.getAttribute('src')).toBe('/on.jpg'); expect(on.hasAttribute('data-sheet-held')).toBe(false);
    expect(off.getAttribute('src')).toBeNull(); expect(off.hasAttribute('data-sheet-held')).toBe(true);
    endSheetPriority();
    expect(off.getAttribute('src')).toBe('/off.jpg'); expect(off.hasAttribute('data-sheet-held')).toBe(false);
  });
});
