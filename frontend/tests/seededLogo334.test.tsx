import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, renderHook, act, cleanup } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { SeededLogo, seededTitleStyle, useLogoSwap } from '@/components/media/SeededLogo';
import { MOTION } from '@/theme/motion';

let pending: Record<string, () => void> = {};
class FakeImage {
  onload: (() => void) | null = null; onerror: (() => void) | null = null;
  decode = vi.fn(() => Promise.resolve());
  set src(v: string) { pending[v] = () => this.onload?.(); }
}
const realImage = globalThis.Image;
beforeEach(() => { pending = {}; (globalThis as { Image: unknown }).Image = FakeImage; });
afterEach(() => { cleanup(); (globalThis as { Image: unknown }).Image = realImage; });
const load = async (u: string) => { await act(async () => { pending[u]!(); await Promise.resolve(); await Promise.resolve(); }); };

describe('seeded clearlogo', () => {
  it('waits for both the logo and the sharp art to decode', async () => {
    const { result } = renderHook(() => useLogoSwap(true, '/logo.png', '/art-w1280.jpg'));
    expect(result.current).toBe(false);
    await load('/logo.png');
    expect(result.current).toBe(false);
    await load('/art-w1280.jpg');
    expect(result.current).toBe(true);
  });
  it('no logo or not seeded: never swaps', () => {
    expect(renderHook(() => useLogoSwap(true, null, '/a.jpg')).result.current).toBe(false);
    expect(renderHook(() => useLogoSwap(false, '/l.png', '/a.jpg')).result.current).toBe(false);
  });
  it('fades in over 200ms, absolutely placed in the title box', () => {
    const calls: unknown[][] = [];
    (Element.prototype as unknown as { animate: unknown }).animate = (...a: unknown[]) => { calls.push(a); return {} as Animation; };
    const { getByTestId } = render(<SeededLogo url="/logo.png" alt="Dune" />);
    expect(getByTestId('seeded-logo').style.position).toBe('absolute');
    expect(calls[0]![0]).toEqual([{ opacity: 0 }, { opacity: 1 }]);
    expect((calls[0]![1] as KeyframeAnimationOptions).duration).toBe(MOTION.dur.backdropSwap);
    expect(seededTitleStyle(true).opacity).toBe(0);
    expect(seededTitleStyle(false).opacity).toBe(1);
  });
  it('movie and TV sheets render the seeded logo in a relative title box', () => {
    for (const f of ['MovieDetailModal', 'TvDetailModal']) {
      const s = readFileSync(resolve(__dirname, `../src/components/media/${f}/CompactLayout.tsx`), 'utf8');
      expect(s).toMatch(/useLogoSwap\(hero\.seeded/);
      expect(s).toMatch(/<SeededLogo url=\{seededLogo\}/);
      expect(s).toMatch(/data-title-box=""\n\s+style=\{\{\n\s+position: 'relative'/);
    }
  });
});
