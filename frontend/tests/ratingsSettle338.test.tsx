import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { renderHook, act, cleanup, render } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { useRatingsSettle, RATINGS_CAP_MS } from '@/hooks/useRatingsSettle';
import { RatingsSettle } from '@/components/media/RatingsSettle';
import { ratingsDone } from '@/hooks/useImdbRatings';
import { MOTION } from '@/theme/motion';
import type { Ratings } from '@/utils/modalParsers';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
class FakeImage { src = ''; decode = () => Promise.resolve(); }
beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };

const R = (p: Partial<Ratings>): Ratings => ({ imdb: null, rotten_tomatoes_critic: null, rotten_tomatoes_audience: null, metacritic: null, tmdb: null, ...p } as Ratings);
const imdb = R({ imdb: { score: 7.9 } as Ratings['imdb'] });
const all = R({ imdb: { score: 7.9 } as Ratings['imdb'], rotten_tomatoes_critic: { score: 94 } as Ratings['rotten_tomatoes_critic'], metacritic: { score: 81 } as Ratings['metacritic'] });
type P = { v: Ratings | null; s: boolean; k: string };
const mount = (p: P) => renderHook(({ v, s, k }: P) => useRatingsSettle(v, s, k), { initialProps: p });

describe('ratings settle into one row', () => {
  it('all at once: nothing while sources are out, then every badge together', async () => {
    const h = mount({ v: imdb, s: false, k: 'a' });
    await flush();
    expect(h.result.current.shown).toBe(false);
    h.rerender({ v: all, s: true, k: 'a' });
    await flush();
    expect(h.result.current).toEqual({ shown: true, ratings: all });
  });

  it('cap with a slow source: at ~1s whatever is ready shows, together', async () => {
    const h = mount({ v: imdb, s: false, k: 'a' });
    await flush();
    act(() => { vi.advanceTimersByTime(RATINGS_CAP_MS - 1); });
    expect(h.result.current.shown).toBe(false);
    act(() => { vi.advanceTimersByTime(1); });
    await flush();
    expect(h.result.current).toEqual({ shown: true, ratings: imdb });
  });

  it('a source answering after the reveal is dropped for that open', async () => {
    const h = mount({ v: imdb, s: true, k: 'a' });
    await flush();
    expect(h.result.current.ratings).toBe(imdb);
    h.rerender({ v: all, s: true, k: 'a' });
    await flush();
    act(() => { vi.advanceTimersByTime(5000); });
    expect(h.result.current.ratings).toBe(imdb);
  });

  it('failed sources: the row settles (empty) and stays empty, no late pop-in', async () => {
    const h = mount({ v: null, s: true, k: 'a' });
    await flush();
    expect(h.result.current).toEqual({ shown: true, ratings: null });
    h.rerender({ v: all, s: true, k: 'a' });
    await flush();
    expect(h.result.current.ratings).toBeNull();
  });

  it('one failed source among others: the answered ones show together', async () => {
    const h = mount({ v: imdb, s: true, k: 'a' });   // e.g. OMDb answered, the RT fill failed (settled)
    await flush();
    expect(h.result.current).toEqual({ shown: true, ratings: imdb });
  });

  it('a new open starts fresh', async () => {
    const h = mount({ v: imdb, s: true, k: 'a' });
    await flush();
    h.rerender({ v: null, s: false, k: 'b' });
    expect(h.result.current.shown).toBe(false);
  });

  it('the row reserves its height and fades in once with the content-settle fade', () => {
    const hold = render(<RatingsSettle settle={{ shown: false, ratings: null }} size="sm" reserve={32} />);
    const el = hold.container.firstChild as HTMLElement;
    expect([el.style.minHeight, el.style.opacity]).toEqual(['32px', '0']);
    expect(el.style.transition).toBe(`opacity ${MOTION.dur.contentFade}ms ${MOTION.ease.contentFade}`);
    hold.rerender(<RatingsSettle settle={{ shown: true, ratings: all }} size="sm" reserve={32} />);
    expect(el.style.opacity).toBe('1');
    expect(el.querySelectorAll('img').length).toBe(3);
  });

  it('TV ratings count as answered only for the current title (a failure counts)', () => {
    expect(ratingsDone({ ratings: null, loading: false }, 'Severance')).toBe(false);
    expect(ratingsDone({ ratings: null, loading: true, doneFor: 'Severance' }, 'Severance')).toBe(false);
    expect(ratingsDone({ ratings: null, loading: false, doneFor: 'Severance' }, 'Severance')).toBe(true);
    expect(ratingsDone({ ratings: null, loading: false, doneFor: 'Other' }, 'Severance')).toBe(false);
  });

  it('all four sheets render the settled row, not the raw ratings', () => {
    for (const f of ['MovieDetailModal/CompactLayout', 'MovieDetailModal/ExpandedLayout', 'TvDetailModal/CompactLayout', 'TvDetailModal/ExpandedLayout']) {
      const s = src(`components/media/${f}.tsx`);
      expect(s).toMatch(/useRatingsSettle\(/);
      expect(s).toMatch(/<RatingsSettle settle=\{ratingsSettle\}/);
    }
    expect(src('hooks/useMovieDetail.ts')).toMatch(/\.finally\(\(\) => \{ if \(!cancelled\) setRatingsSettled\(true\); \}\)/);
  });

  type A = { v: Ratings | null; s: boolean; k: string; a: boolean };
  const mountA = (p: A) => renderHook(({ v, s, k, a }: A) => useRatingsSettle(v, s, k, a), { initialProps: p });

  it('empty at the 1s cap does not lock: ratings at 1.6s show once, later sources still dropped', async () => {
    const h = mountA({ v: null, s: false, k: 'a', a: true });
    act(() => { vi.advanceTimersByTime(RATINGS_CAP_MS); });
    await flush();
    expect(h.result.current).toEqual({ shown: false, ratings: null });
    act(() => { vi.advanceTimersByTime(600); });
    h.rerender({ v: imdb, s: false, k: 'a', a: true });
    await flush();
    expect(h.result.current).toEqual({ shown: true, ratings: imdb });
    const el = render(<RatingsSettle settle={h.result.current} size="sm" reserve={32} />).container.firstChild as HTMLElement;
    expect(el.style.transition).toBe(`opacity ${MOTION.dur.contentFade}ms ${MOTION.ease.contentFade}`);
    h.rerender({ v: all, s: true, k: 'a', a: true });
    await flush();
    act(() => { vi.advanceTimersByTime(5000); });
    expect(h.result.current.ratings).toBe(imdb);
  });

  it('empty past the cap: the reserved empty row once every source has failed, then frozen', async () => {
    const h = mountA({ v: null, s: false, k: 'a', a: true });
    act(() => { vi.advanceTimersByTime(3000); });
    await flush();
    expect(h.result.current.shown).toBe(false);
    h.rerender({ v: null, s: true, k: 'a', a: true });
    await flush();
    expect(h.result.current).toEqual({ shown: true, ratings: null });
    h.rerender({ v: all, s: true, k: 'a', a: true });
    await flush();
    expect(h.result.current.ratings).toBeNull();
  });

  it('the cap starts when the details land, not at the tap', async () => {
    const h = mountA({ v: null, s: false, k: 'a', a: false });
    act(() => { vi.advanceTimersByTime(2000); });
    h.rerender({ v: imdb, s: false, k: 'a', a: true });
    await flush();
    expect(h.result.current.shown).toBe(false);
    act(() => { vi.advanceTimersByTime(RATINGS_CAP_MS - 1); });
    await flush();
    expect(h.result.current.shown).toBe(false);
    act(() => { vi.advanceTimersByTime(1); });
    await flush();
    expect(h.result.current).toEqual({ shown: true, ratings: imdb });
  });

  it('desktop: the reserved height equals the rendered lg bar (margin + 36px line), the all-fail row too', async () => {
    const { RATINGS_SLOT_LG } = await import('@/components/media/RatingsBar');
    expect(RATINGS_SLOT_LG).toBe(50);
    const r = render(<RatingsSettle settle={{ shown: true, ratings: all }} size="lg" reserve={RATINGS_SLOT_LG} />);
    const bar = (r.container.firstChild as HTMLElement).firstChild as HTMLElement;
    const lineH = [...bar.querySelectorAll('span')].map(sp => sp.style.lineHeight);
    expect(lineH.every(h => h === '36px')).toBe(true);
    expect(parseInt(bar.style.marginTop) + parseInt(bar.style.minHeight)).toBe(RATINGS_SLOT_LG);
    [...bar.querySelectorAll('img')].forEach(i => expect(parseInt(i.style.height)).toBeLessThanOrEqual(36));
    const empty = render(<RatingsSettle settle={{ shown: true, ratings: null }} size="lg" reserve={RATINGS_SLOT_LG} />);
    expect((empty.container.firstChild as HTMLElement).style.minHeight).toBe('50px');
  });
});
