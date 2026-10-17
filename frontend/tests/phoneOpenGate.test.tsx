import { act, render, renderHook } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/utils/posterColors', async (orig) => ({
  ...(await orig<typeof import('@/utils/posterColors')>()),
  colorsFromImage: vi.fn(() => ({ topLeft: 'rgb(200, 40, 40)', topRight: 'rgb(180, 60, 20)', bottomLeft: 'rgb(90, 20, 20)', bottomRight: 'rgb(120, 30, 10)' })),
}));

import { openWhenReady, scheduleWarm, OPEN_CAP_MS, REVEALED_EVENT, WARM_CONCURRENCY, _resetModalPrefetch } from '@/services/modalPrefetch';
import { getSeedColors, _resetModalColors } from '@/services/modalColors';
import { useModalColors } from '@/hooks/useModalColors';
import { sheetBase } from '@/utils/posterColors';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const never = () => new Promise<Response>(() => {});

describe('phone popup opens only when composed', () => {
  beforeEach(() => { _resetModalPrefetch(); _resetModalColors(); vi.useFakeTimers(); vi.stubGlobal('fetch', vi.fn(never)); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('does not open on the tap; the poster shows pressed, then it opens at the cap', async () => {
    const card = document.createElement('button');
    card.appendChild(document.createElement('img'));
    const open = vi.fn();
    void openWhenReady('movie', 21, undefined, card, open);
    expect(open).not.toHaveBeenCalled();
    expect(card.getAttribute('data-pressed')).toBe('true');
    await act(async () => { await vi.advanceTimersByTimeAsync(OPEN_CAP_MS - 50); });
    expect(open).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(60); });
    expect(open).toHaveBeenCalledTimes(1);
    // Still pressed until the sheet shows composed.
    expect(card.getAttribute('data-pressed')).toBe('true');
    window.dispatchEvent(new Event(REVEALED_EVENT));
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
    it(`${f}: stays off-screen until composed and paints the blur base, not navy`, () => {
      const s = read(f);
      expect(s).toContain("phase === 'entering' || !composed\n      ? 'translate3d(0, 100%, 0)'");
      expect(s).toContain("opacity: phase === 'entering' || !composed ? 0 : 1");
      expect(s).toContain("const sheetBg = sheetBase(ultraBlurColors) ?? '#15102a'");
      expect(s).not.toContain("backgroundColor: '#15102a'");
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
