import { act, render, renderHook } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/utils/posterColors', async (orig) => ({
  ...(await orig<typeof import('@/utils/posterColors')>()),
  colorsFromImage: vi.fn(() => ({ topLeft: 'rgb(200, 40, 40)', topRight: 'rgb(180, 60, 20)', bottomLeft: 'rgb(90, 20, 20)', bottomRight: 'rgb(120, 30, 10)' })),
}));

import { openWhenReady, scheduleWarm, OPEN_CAP_MS, OPEN_WAIT_MS, SHEET_MOVING_EVENT, WARM_CONCURRENCY, _resetModalPrefetch } from '@/services/modalPrefetch';
import { getSeedColors, _resetModalColors } from '@/services/modalColors';
import { useModalColors } from '@/hooks/useModalColors';
import { sheetBase } from '@/utils/posterColors';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const never = () => new Promise<Response>(() => {});

describe('phone popup opens only when composed', () => {
  beforeEach(() => { _resetModalPrefetch(); _resetModalColors(); vi.useFakeTimers(); vi.stubGlobal('fetch', vi.fn(never)); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('v325 motion P1: waits at most OPEN_WAIT_MS (40 ms, inside the 150 ms tap-to-motion budget) for a cold title, pressed meanwhile; the press lifts when the sheet moves', async () => {
    const card = document.createElement('button');
    card.appendChild(document.createElement('img'));
    const open = vi.fn();
    void openWhenReady('movie', 21, undefined, card, open);
    expect(open).not.toHaveBeenCalled();
    expect(card.getAttribute('data-pressed')).toBe('true');
    await act(async () => { await vi.advanceTimersByTimeAsync(OPEN_WAIT_MS + 10); });
    expect(open).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(20); });
    // Held until the sheet's first moving frame (Designer 10:16 PM), not released on open.
    expect(card.getAttribute('data-pressed')).toBe('true');
    window.dispatchEvent(new Event(SHEET_MOVING_EVENT));
    expect(card.hasAttribute('data-pressed')).toBe(false);
  });

  it('ignores a second tap while the first is getting ready', async () => {
    const open = vi.fn();
    void openWhenReady('movie', 1, undefined, null, open);
    void openWhenReady('movie', 2, undefined, null, open);
    await act(async () => { await vi.advanceTimersByTimeAsync(OPEN_CAP_MS + 10); });
    expect(open).toHaveBeenCalledTimes(1);
  });

  it('seeds the color blur from the tapped poster, so the first background is never navy', async () => {
    const card = document.createElement('button');
    card.appendChild(document.createElement('img'));
    void openWhenReady('tv', 31, undefined, card, () => {});
    const seed = getSeedColors('tv', 31);
    expect(seed).toBeTruthy();
    const { result } = renderHook(() => useModalColors('tv', 31, undefined));
    expect(result.current.colors).toEqual(seed);
    expect(result.current.settled).toBe(true);
    const bg = sheetBase(result.current.colors)!;
    expect(bg).toBeTruthy();
    expect(bg.toLowerCase()).not.toBe('#15102a');
    expect(bg).not.toBe('rgb(21, 16, 42)');
    await act(async () => { await vi.advanceTimersByTimeAsync(OPEN_CAP_MS + 10); });
  });
});

describe('phone card warming', () => {
  beforeEach(() => { _resetModalPrefetch(); });

  it(`runs at most ${WARM_CONCURRENCY} at once and drops cards that scrolled away`, async () => {
    const releases: (() => void)[] = [];
    const runs: string[] = [];
    const job = (k: string) => () => new Promise<void>((res) => { runs.push(k); releases.push(res); });
    const cancels = ['a', 'b', 'c', 'd', 'e', 'f'].map((k) => scheduleWarm(k, job(k)));
    expect(runs).toEqual(['a', 'b', 'c', 'd']);
    cancels[4]!(); // e scrolled away before its turn
    releases[0]!();
    await new Promise((r) => setTimeout(r, 0));
    expect(runs).toEqual(['a', 'b', 'c', 'd', 'f']);
  });
});

describe('phone sheet markup', () => {
  for (const f of ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/CompactLayout.tsx']) {
    it(`${f}: v325 motion P1: SheetFrame slides an opaque card in, seed shell first, never navy`, () => {
      const s = read(f);
      expect(s).toContain('<SheetFrame');
      expect(s).toContain('composed={composed}');
      expect(s).toContain('<SeedLayer show={shellShown.current} ready={hero.seeded ? detailsIn : composed}');
      // No invisible-until-composed sheet; only the title holds for the logo.
      expect(s).not.toContain('cardOpacity');
      expect(s).not.toContain("opacity: titleState === 'hold' ? 0 : 1");
      expect(s).toContain('hold={heroHold} instant');
      expect(s).toContain("const heroHold = hero.seeded ? false : titleState === 'hold';");
      expect(s).toContain('ready={composed}');
      expect(s).toContain('const sheetBg = sheetBase(ultraBlurColors) ?? APP_BG');
      expect(s).not.toContain('#15102a');
    });
  }
});

describe('blur crossfade', () => {
  it('keeps the old palette under the new one while it fades in', async () => {
    const { UltraBlurOverlay } = await import('@/components/media/UltraBlurOverlay');
    const a = { topLeft: 'rgb(1, 2, 3)', topRight: 'rgb(1, 2, 3)', bottomLeft: 'rgb(1, 2, 3)', bottomRight: 'rgb(1, 2, 3)' };
    const b = { topLeft: 'rgb(9, 8, 7)', topRight: 'rgb(9, 8, 7)', bottomLeft: 'rgb(9, 8, 7)', bottomRight: 'rgb(9, 8, 7)' };
    const { container, rerender } = render(<div><UltraBlurOverlay colors={a as never} /></div>);
    expect(container.querySelector('[data-blur-crossfade]')).toBeNull();
    rerender(<div><UltraBlurOverlay colors={b as never} /></div>);
    expect(container.querySelector('[data-blur-crossfade]')).toBeTruthy();
  });
});

describe('designer NO fixes on 15a4f789', () => {
  const read = (p: string) => require('node:fs').readFileSync(p, 'utf8') as string;
  it('the seed draws only hero + title (no placeholder bars) and fades out over the details', () => {
    const s = read('src/components/layout/SheetFrame.tsx');
    const seed = s.slice(s.indexOf('export function SeedShell'), s.indexOf('export type SeedState'));
    expect(seed).not.toContain('--m-placeholder');
    expect(s).toContain('export function SeedLayer');
  });
  it('phone search surfaces and the sheet base mix sit on #111318', () => {
    expect(read('src/components/search/SearchShelf.tsx')).not.toContain('var(--color-kiosk-black)');
    expect(read('src/components/mobile/MobileSearchOverlay.tsx')).not.toMatch(/kiosk-black/);
    expect(read('src/utils/posterColors.ts')).toContain('[0x11, 0x13, 0x18]');
  });
  it('the desktop series page loads Google Sans Flex', () => {
    const css = read('src/index.css');
    expect(css).toContain("url('/fonts/GoogleSansFlex-latin.woff2')");
    expect(css).toContain("html[data-luma-surface='series'] #root *");
  });
});
