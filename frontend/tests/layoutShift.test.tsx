import { act, render } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it, vi } from 'vitest';
import { RatingsBar, RATINGS_SLOT_SM } from '@/components/media/RatingsBar';
import { CastPhoto } from '@/components/media/CastPhoto';
import { RATING_ICONS } from '@/utils/ratingIcons';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');

describe('no layout shift from late images', () => {
  it('rating icons have a fixed box (width and height) before they load', () => {
    const { container } = render(<RatingsBar size="sm" ratings={{
      imdb: { score: 8.1 }, rotten_tomatoes_critic: { score: 91 }, rotten_tomatoes_audience: { score: 40 }, metacritic: { score: 70 },
    } as never} />);
    const imgs = [...container.querySelectorAll('img')];
    expect(imgs.length).toBe(4);
    for (const img of imgs) {
      expect(Number(img.getAttribute('width'))).toBeGreaterThan(0);
      expect(img.getAttribute('height')).toBe('20');
      expect(img.style.width).not.toBe('auto');
      expect(img.style.width).toMatch(/^\d+px$/);
    }
    const imdb = imgs.find((i) => i.getAttribute('src') === RATING_ICONS.imdb)!;
    expect(imdb.getAttribute('width')).toBe(String(Math.round(20 * 595 / 362)));
  });

  it('a cast photo swaps in with the same block box as its placeholder', async () => {
    const loads: (() => void)[] = [];
    vi.stubGlobal('Image', class { onload: (() => void) | null = null; set src(_v: string) { loads.push(() => this.onload?.()); } decode() { return Promise.resolve(); } });
    const style = { width: '90px', height: '90px', borderRadius: '50%', marginBottom: '8px' };
    const { container } = render(<CastPhoto src="/p.jpg" alt="A" ready fallback="/f.svg" style={style} />);
    const ph = container.firstElementChild as HTMLElement;
    expect(ph.style.display).toBe('block');
    await act(async () => { loads[0]!(); await Promise.resolve(); });
    const img = container.querySelector('img')!;
    expect(img.style.display).toBe('block');
    expect(img.getAttribute('width')).toBe('90');
    expect(img.getAttribute('height')).toBe('90');
    expect(img.style.width).toBe(ph.style.width);
    expect(img.style.height).toBe(ph.style.height);
    vi.unstubAllGlobals();
  });

  it('the phone movie sheet reserves the ratings row while they load and never collapses it', () => {
    const s = read('components/media/MovieDetailModal/CompactLayout.tsx');
    expect(s).toContain("<div data-ratings-slot style={{ minHeight: reserveRatings ? `${RATINGS_SLOT_SM}px` : 0 }}>");
    expect(s).toContain('if (composed && (ratingsLoading || hasRatings)) setReserveRatings(true);');
    expect(RATINGS_SLOT_SM).toBe(32);
    expect(read('components/media/MovieDetailModal/index.tsx')).toContain('    ratingsLoading,\n');
  });
});
