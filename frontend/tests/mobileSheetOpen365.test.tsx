/** v365: the phone movie/TV sheet opens by sliding at once, its blur is the sheet's own backdrop (inside the sliding
 *  card), draws on one palette (neutral, then the final palette fades in once, never the poster seed), and costs no
 *  filter raster on the slide's frames. Close, drag-to-dismiss and the edge-swipe back keep their bytes. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, renderHook, act } from '@testing-library/react';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

vi.mock('@/apiClient', () => ({
  apiFetch: () => new Promise((res) => setTimeout(() => res({ ok: true, json: () => Promise.resolve({ topLeft: '#112233', topRight: '#223344' }) }), 50)),
}));

import { UltraBlurOverlay, blobGradient } from '@/components/media/UltraBlurOverlay';
import { PaletteFade, useSheetPaletteAt } from '@/components/layout/SheetFrame';
import { SHEET_NEUTRAL } from '@/theme/motion';
import { modalColors, peekModalColors, _resetModalColors, setSeedColors } from '@/services/modalColors';
import { useModalColors } from '@/hooks/useModalColors';

const SRC = path.resolve(__dirname, '../src');
const read = (f: string) => fs.readFileSync(path.join(SRC, f), 'utf8');
const sha = (f: string) => crypto.createHash('sha256').update(fs.readFileSync(path.join(SRC, f))).digest('hex');

const FINAL = { topLeft: '#3d7a1f', topRight: '#8cc22b', bottomRight: '#1f5a7a', bottomLeft: '#6b1f7a' };
const SEED = { topLeft: '#808080', topRight: '#909090', bottomRight: '#707070', bottomLeft: '#606060' };

describe('v365: the phone sheet draws one palette', () => {
  beforeEach(() => { vi.useFakeTimers(); _resetModalColors(); });
  afterEach(() => { vi.useRealTimers(); });

  it('the phone sheet gets the fetched palette alone, never the poster seed', async () => {
    setSeedColors('movie', 9, 5, SEED);
    const { result } = renderHook(() => useModalColors('movie', 9, 5));
    expect(result.current.colors).toEqual(SEED);
    expect(result.current.fetched).toBeNull();
    await act(async () => { vi.advanceTimersByTime(100); for (let i = 0; i < 6; i++) await Promise.resolve(); });
    expect(result.current.fetched).toEqual({ topLeft: '#112233', topRight: '#223344' });
  });

  it('a palette that answered during the warm-up is there at the first render', async () => {
    const p = modalColors('movie', 9, 5);
    await act(async () => { vi.advanceTimersByTime(100); await p; });
    expect(peekModalColors('movie', 9, 5)).toEqual({ topLeft: '#112233', topRight: '#223344' });
    const { result } = renderHook(() => useModalColors('movie', 9, 5));
    expect(result.current.fetched).toEqual({ topLeft: '#112233', topRight: '#223344' });
  });

  it('a palette present at the first render shows at once; one that lands later fades in once', () => {
    const early = renderHook(({ c }) => useSheetPaletteAt(c), { initialProps: { c: FINAL as typeof FINAL | null } });
    expect(early.result.current).toBe(-1);
    const late = renderHook(({ c }) => useSheetPaletteAt(c), { initialProps: { c: null as typeof FINAL | null } });
    expect(late.result.current).toBeNull();
    late.rerender({ c: FINAL });
    expect(late.result.current).toBeGreaterThanOrEqual(0);
    const at = late.result.current;
    late.rerender({ c: { ...FINAL, topLeft: '#000000' } });
    expect(late.result.current).toBe(at);

    const shown = render(<PaletteFade colors={FINAL} at={-1}><i /></PaletteFade>);
    expect(shown.container.querySelector('[data-palette-fade]')).toBeNull();
    const fading = render(<PaletteFade colors={FINAL} at={performance.now()}><i /></PaletteFade>);
    const el = fading.container.querySelector<HTMLElement>('[data-palette-fade]')!;
    expect(el.style.animation).toContain('m-crossfade');
    const none = render(<PaletteFade colors={null} at={null}><i /></PaletteFade>);
    expect(none.container.innerHTML).toBe('');
  });

  it('the neutral is #15102a and both phone sheets start on it and fade the tint and blur in', () => {
    expect(SHEET_NEUTRAL).toBe('#15102a');
    for (const f of ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/CompactLayout.tsx']) {
      const s = read(f);
      expect(s, f).toContain('<SheetCard background={SHEET_NEUTRAL} tint={sheetBase(ultraBlurColors)} hold={reveal.revealed}>');
      expect(s, f).toContain('useSheetPaletteAt(ultraBlurColors)');
      expect(s, f).toContain('<PaletteFade colors={ultraBlurColors} at={paletteAt}>');
      expect(s, f).not.toContain('animationDelay={300}');
      expect(s, f).toMatch(/useSeededHero\(composed, (paletteColors|detailColors)\)/);
    }
    expect(read('components/media/MovieDetailModal/CompactLayout.tsx')).toContain('sheetBase(ultraBlurColors) ?? SHEET_NEUTRAL');
  });
});

describe('v365: the phone blur costs no filter raster', () => {
  it('the phone blur is one element of radial gradients, with no blur filter, no mask, no per-blob element', () => {
    const { container } = render(<div style={{ width: 390, height: 844 }}><UltraBlurOverlay colors={FINAL} variant="mobile" /></div>);
    const el = container.querySelector<HTMLElement>('[data-ultrablur-overlay]')!;
    expect(el.hasAttribute('data-blur-flat')).toBe(true);
    const g = blobGradient({ color: '#3d7a1f', x: 0.12, y: 0.2 } as Parameters<typeof blobGradient>[0], 65, 0.6, 1.4, 1.25);
    expect(g).toMatch(/^radial-gradient\(ellipse calc\(32\.5% \+ 260px\)/);
    expect(g).not.toMatch(/blur\(/);
    expect(read('components/media/UltraBlurOverlay.tsx')).toContain('backgroundImage: layers');
    expect(el.outerHTML).not.toMatch(/blur\(/);
    expect(el.children.length).toBe(0);
  });

  it('the web, tablet and lite blurs still use the filter', () => {
    for (const props of [{ variant: 'web' as const }, { variant: 'tablet' as const }, { variant: 'mobile' as const, lite: true }]) {
      const { container, unmount } = render(<UltraBlurOverlay colors={FINAL} {...props} />);
      expect(container.querySelector('[data-blur-flat]')).toBeNull();
      unmount();
    }
    const { container } = render(<UltraBlurOverlay colors={FINAL} variant="web" />);
    expect(container.innerHTML).toMatch(/filter: blur\(/);
  });
});

describe('v365: close, drag-to-dismiss and the edge-swipe back are untouched', () => {
  it('the swipe hook and the presence hook keep their SW 364 bytes', () => {
    expect(sha('hooks/useSwipeToDismiss.ts')).toBe('23cc95b91096f8c2eef2b8b6e69a60d47e20d803dc9ed6756327d61719078023');
    expect(sha('components/motion/useSurfacePresence.ts')).toBe('ecd5dad4728445b4652fe6dc5774acaa118f29c488a972befa3c203f8dcab2cd');
  });

  it('SheetFrame keeps its enter, exit, drag-follow and edge-back code (the frame, scrim and card) as of SW 364', () => {
    const s = read('components/layout/SheetFrame.tsx');
    const frame = s.slice(s.indexOf('export function SheetFrame('), s.indexOf('/** The sheet card'));
    const body = frame.replace('export { SHEET_NEUTRAL };\n\n', '');
    expect(crypto.createHash('sha256').update(body).digest('hex')).toBe('103719ba2de0438abdff15517639d9af8f2a80a59dde6fc939b40be3824d3151');
  });
});
