/** v366: the phone movie/TV sheet rises carrying the tapped card's poster and title, from the slide's first frame, on
 *  every open path (Home rails in the full app, the Libraries grid, search), so the list is never followed by an empty
 *  blur. Nothing but the frame, the opacity-only scrim and the sliding card is ever full-viewport. Close, drag-to-dismiss
 *  and the edge-swipe back keep their SW 365 bytes. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

import { SeedArt, SeedLayer, SEED_HERO_FADE_OUT, SEED_FADE_OUT } from '@/components/layout/SheetFrame';
import { SHEET_MOUNT_WAIT_MS, tapShell } from '@/services/modalPrefetch';

const SRC = path.resolve(__dirname, '../src');
const read = (f: string) => fs.readFileSync(path.join(SRC, f), 'utf8');
const sha = (f: string) => crypto.createHash('sha256').update(fs.readFileSync(path.join(SRC, f))).digest('hex');
const LAYOUTS = ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/CompactLayout.tsx'];

const effects = { headerContainerMask: {}, headerImgStyle: { width: '100%', height: '100%', objectFit: 'cover' } } as never;

describe('v366: the seed is the tapped poster and title, carried by the sliding card', () => {
  it('SeedArt is the details\' hero box with the poster decoded for the first frame', () => {
    const { container } = render(<SeedArt src="/p.jpg" effects={effects} />);
    const art = container.querySelector<HTMLElement>('[data-seed-art]')!;
    expect(art).not.toBeNull();
    const img = art.querySelector('img')!;
    expect(img.getAttribute('src')).toBe('/p.jpg');
    expect(img.getAttribute('decoding')).toBe('sync');
    expect(art.outerHTML).not.toMatch(/blur\(|backdrop-filter|backdropFilter/);
  });

  it('a poster seed fades out when the details are ready; an own-hero seed still leaves at once', () => {
    const hero = (fade: boolean) => ({ art: <i data-art />, wash: null, title: <b data-title />, fade });
    const ready = render(<SeedLayer show ready overlap={100} hero={hero(true)} />);
    const layer = ready.container.querySelector<HTMLElement>('[data-seed-layer]')!;
    expect(layer.getAttribute('data-seed-layer')).toBe('fading');
    expect(layer.style.animation).toBe(SEED_HERO_FADE_OUT);
    expect(SEED_HERO_FADE_OUT).toBe('m-crossfade var(--m-dur-reveal) var(--m-ease-reveal) reverse both');
    expect(SEED_FADE_OUT).toBe('m-crossfade var(--m-dur-crossfade) var(--m-ease-standard) reverse both');
    const own = render(<SeedLayer show ready overlap={100} hero={hero(false)} />);
    expect(own.container.querySelector('[data-seed-layer]')).toBeNull();
    const waiting = render(<SeedLayer show ready={false} overlap={100} hero={hero(true)} />);
    const on = waiting.container.querySelector<HTMLElement>('[data-seed-layer]')!;
    expect(on.getAttribute('data-seed-layer')).toBe('on');
    expect(on.querySelector('[data-art]')).not.toBeNull();
    expect(on.querySelector('[data-title]')).not.toBeNull();
  });

  it('movie and TV phone sheets build the same seed (poster art, visible title, fade until the reveal)', () => {
    for (const f of LAYOUTS) {
      const s = read(f);
      expect(s, f).toContain('SeedArt');
      expect(s, f).toContain('<SeedArt src={hero.art} effects={backdropEffects} />');
      expect(s, f).toContain('fade: !!hero.art');
      expect(s, f).toContain('title: hero.art ? seedTitleEl : titleEl');
      expect(s, f).toContain('ready={hero.seeded ? seedDone : composed}');
      expect(s, f).toContain('const seedDone = hero.art ? (detailsIn && reveal.revealed) || (!loading && !!error) : detailsIn;');
      expect(s, f).toContain('data-seed-title=""');
    }
  });
});

describe('v366: a card that opens its sheet without openWhenReady still hands over its poster and title', () => {
  beforeEach(() => { vi.useFakeTimers(); document.body.innerHTML = ''; });
  afterEach(() => { vi.useRealTimers(); document.body.innerHTML = ''; });

  it('a click on a poster card is noted for the sheet that mounts next, and expires', () => {
    document.body.innerHTML = '<button id="c"><img src="/poster.jpg" alt="Super Size Me"></button><button id="n">No art</button>';
    document.getElementById('n')!.click();
    expect(tapShell()).toBeNull();
    document.getElementById('c')!.click();
    const s = tapShell();
    expect(s?.title).toBe('Super Size Me');
    expect(s?.poster).toContain('/poster.jpg');
    expect(s?.generic).toBe(true);
    vi.advanceTimersByTime(SHEET_MOUNT_WAIT_MS + 1);
    expect(tapShell()).toBeNull();
  });

  it('the click listener only notes; every open path (Home rail, Libraries grid, search) is unchanged otherwise', () => {
    const s = read('services/modalPrefetch.ts');
    expect(s).toContain("document.addEventListener('click', noteCardTap, { capture: true, passive: true });");
    expect(s).not.toMatch(/function noteCardTap[\s\S]{0,900}preventDefault|function noteCardTap[\s\S]{0,900}stopPropagation/);
  });
});

describe('v366: nothing full-viewport shows before or apart from the sliding sheet', () => {
  const frame = () => {
    const s = read('components/layout/SheetFrame.tsx');
    return s.slice(s.indexOf('export function SheetFrame('), s.indexOf('/** The sheet card'));
  };

  it('the frame holds one transparent wrapper, an opacity-only scrim and the card: no filter, no backdrop-filter', () => {
    const f = frame();
    expect(f).not.toMatch(/backdropFilter|backdrop-filter|filter:|blur\(/);
    expect((f.match(/data-sheet-scrim/g) || []).length).toBeGreaterThan(0);
    const all = read('components/layout/SheetFrame.tsx');
    expect(all).not.toMatch(/backdropFilter|backdrop-filter/);
    expect(all).not.toMatch(/filter: ?['"`]?blur/);
  });

  it('the phone blur stays one gradient element inside the card (no filter raster, from SW 365)', () => {
    expect(read('components/media/UltraBlurOverlay.tsx')).toContain('backgroundImage: layers');
    for (const f of LAYOUTS) expect(read(f), f).toContain('<SheetCard background={SHEET_NEUTRAL}');
  });
});

describe('v366: close, drag-to-dismiss and the edge-swipe back keep their SW 365 bytes', () => {
  it('the swipe hook and the presence hook are unchanged', () => {
    expect(sha('hooks/useSwipeToDismiss.ts')).toBe('23cc95b91096f8c2eef2b8b6e69a60d47e20d803dc9ed6756327d61719078023');
    expect(sha('components/motion/useSurfacePresence.ts')).toBe('ecd5dad4728445b4652fe6dc5774acaa118f29c488a972befa3c203f8dcab2cd');
  });

  it('SheetFrame keeps its enter, exit, drag-follow and edge-back code (frame, scrim, card)', () => {
    const s = read('components/layout/SheetFrame.tsx');
    const body = s.slice(s.indexOf('export function SheetFrame('), s.indexOf('/** The sheet card')).replace('export { SHEET_NEUTRAL };\n\n', '');
    expect(crypto.createHash('sha256').update(body).digest('hex')).toBe('103719ba2de0438abdff15517639d9af8f2a80a59dde6fc939b40be3824d3151');
  });
});

describe('v366: service worker and update path', () => {
  const root = path.resolve(__dirname, '../..');
  it('the worker is v366 and the stamp names it', () => {
    expect(fs.readFileSync(path.join(root, 'static/sw.js'), 'utf8')).toContain("const CACHE_VERSION = 'media-kiosk-v366';");
    expect(JSON.parse(fs.readFileSync(path.join(root, 'static/sw-stamp.json'), 'utf8')).cache_version).toBe('media-kiosk-v366');
  });
  it('a resumed installed app asks the worker for an update on every watch check', () => {
    const m = read('main.tsx');
    expect(m).toContain('swRegistration = reg;');
    expect(m).toContain('onCheck: () => { swRegistration?.update().catch(() => {}); }');
  });
});
