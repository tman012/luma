import { describe, expect, it } from 'vitest';
import { MAX_SAFE_BAND, screenGapPx } from '@/hooks/viewportGap';

describe('screen gap', () => {
  it('is the band between the layout edge and a short viewport unit', () => {
    expect(screenGapPx(800, 766)).toBe(34);
    expect(screenGapPx(800.4, 766.2)).toBe(34);
  });

  it('stays 0 when the unit already reaches the edge', () => {
    expect(screenGapPx(800, 800)).toBe(0);
    expect(screenGapPx(800, 799.4)).toBe(0);
  });

  it('ignores a missing measure and a gap too large to be a safe-area band', () => {
    expect(screenGapPx(0, 0)).toBe(0);
    expect(screenGapPx(Number.NaN, 100)).toBe(0);
    expect(screenGapPx(900, 0)).toBe(0);
    expect(screenGapPx(1000, 1000 - MAX_SAFE_BAND - 1)).toBe(0);
  });
});
