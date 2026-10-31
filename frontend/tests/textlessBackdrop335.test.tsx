import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, act, cleanup } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { BackdropSwap } from '@/components/media/BackdropSwap';
import { MOTION } from '@/theme/motion';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('text-free backdrop over Plex art', () => {
  it('BackdropSwap shows nothing until decoded, then fades in over 200ms in the same box', async () => {
    let resolveDecode!: () => void;
    class FakeImage { src = ''; decode = () => new Promise<void>(r => { resolveDecode = r; }); }
    vi.stubGlobal('Image', FakeImage);
    const calls: unknown[][] = [];
    (Element.prototype as unknown as { animate: unknown }).animate = (...a: unknown[]) => { calls.push(a); return {} as Animation; };
    const { container } = render(<BackdropSwap url="/tmdb-textless.jpg" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />);
    expect(container.querySelector('img')).toBeNull();
    await act(async () => { resolveDecode(); await Promise.resolve(); });
    const img = container.querySelector('img')!;
    expect(img.getAttribute('src')).toBe('/tmdb-textless.jpg');
    expect([img.style.position, img.style.top, img.style.left, img.style.width, img.style.height]).toEqual(['absolute', '0px', '0px', '100%', '100%']);
    expect((calls[0]![1] as KeyframeAnimationOptions).duration).toBe(MOTION.dur.backdropSwap);
  });

  it('useTvDetail swaps in the TMDb text-free backdrop only when TMDb reports a logo and a text-free pick', () => {
    const s = src('hooks/useTvDetail.ts');
    expect(s).toMatch(/tmdbData\.has_logo && tmdbData\.backdrop_textless \? parsed\.backdropUrl : null/);
    expect(s).toMatch(/backdropSwapUrl: swap && swap !== prev\.backdropUrl \? swap : null/);
  });

  it('phone and desktop TV sheets render the swap', () => {
    expect(src('components/media/TvDetailModal/CompactLayout.tsx')).toMatch(/upgradeUrl=\{show\.backdropSwapUrl \?/);
    expect(src('components/media/TvDetailModal/ExpandedLayout.tsx')).toMatch(/<BackdropSwap url=\{show\.backdropSwapUrl\}/);
  });
});
