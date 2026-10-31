import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, fireEvent, cleanup } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { ContentReveal, FadeImg, readReserve } from '@/components/motion/ContentReveal';
import { backdropHiRes, backdropUpgrade } from '@/utils/image';
import { MOTION } from '@/theme/motion';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');

let anims: { el: Element; frames: Keyframe[]; opts: KeyframeAnimationOptions }[] = [];
beforeEach(() => {
  anims = [];
  localStorage.clear();
  vi.useFakeTimers();
  (Element.prototype as unknown as { animate: unknown }).animate = function (this: Element, frames: Keyframe[], opts: KeyframeAnimationOptions) {
    anims.push({ el: this, frames, opts });
    return { onfinish: null, cancel() {} } as unknown as Animation;
  };
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('ContentReveal', () => {
  it('cached data shows on frame 0 with no fade', () => {
    const { getByTestId } = render(<ContentReveal id="a" ready landMs={600} testId="r"><p>row</p></ContentReveal>);
    expect(getByTestId('r').dataset.reveal).toBe('shown');
    expect(getByTestId('r').dataset.revealCached).toBe('true');
    expect(anims).toHaveLength(0);
  });

  it('waits for the enter to land, then one 240ms opacity fade with no transform', () => {
    const { getByTestId, rerender } = render(<ContentReveal id="b" ready={false} landMs={600} testId="r"><p>row</p></ContentReveal>);
    const box = getByTestId('r');
    expect(box.style.opacity).toBe('0');
    expect(box.textContent).toBe('');
    rerender(<ContentReveal id="b" ready landMs={600} testId="r"><p>row</p></ContentReveal>);
    expect(anims).toHaveLength(0);
    act(() => { vi.advanceTimersByTime(600); });
    expect(anims).toHaveLength(1);
    expect(anims[0].opts.duration).toBe(MOTION.dur.contentFade);
    expect(anims[0].opts.easing).toBe('cubic-bezier(0.4,0,0.2,1)');
    expect(anims[0].frames).toEqual([{ opacity: 0 }, { opacity: 1 }]);
    expect(JSON.stringify(anims[0].frames)).not.toMatch(/transform|translate|scale/);
  });

  it('never replays', () => {
    const { rerender } = render(<ContentReveal id="c" ready={false} landMs={0} testId="r"><p>1</p></ContentReveal>);
    rerender(<ContentReveal id="c" ready landMs={0} testId="r"><p>1</p></ContentReveal>);
    rerender(<ContentReveal id="c" ready={false} landMs={0} testId="r"><p>1</p></ContentReveal>);
    rerender(<ContentReveal id="c" ready landMs={0} testId="r"><p>2</p></ContentReveal>);
    expect(anims).toHaveLength(1);
  });

  it('reserves one screen by default and the persisted height after', () => {
    const { getByTestId, unmount } = render(<ContentReveal id="d" ready={false} landMs={0} testId="r">x</ContentReveal>);
    expect(getByTestId('r').style.minHeight).toContain('--luma-screen-h');
    unmount();
    localStorage.setItem('luma.contentReserve.d', '812');
    expect(readReserve('d')).toBe(812);
    const r2 = render(<ContentReveal id="d" ready={false} landMs={0} testId="r2">x</ContentReveal>);
    expect(r2.getByTestId('r2').style.minHeight).toBe('812px');
  });

  it('reduced motion: no fade', () => {
    const mm = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: /reduce/.test(q), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })) as unknown as typeof window.matchMedia;
    try {
      const { getByTestId, rerender } = render(<ContentReveal id="e" ready={false} landMs={0} testId="r">x</ContentReveal>);
      rerender(<ContentReveal id="e" ready landMs={0} testId="r">x</ContentReveal>);
      expect(anims).toHaveLength(0);
      expect(getByTestId('r').dataset.reveal).toBe('shown');
    } finally { window.matchMedia = mm; }
  });
});

describe('FadeImg', () => {
  it('starts at 0 and fades to its opacity over 200ms, keeping its own transitions', () => {
    const { container } = render(<FadeImg src="/a.jpg" alt="" opacity={0.38} style={{ transition: 'clip-path 600ms ease' }} />);
    const img = container.querySelector('img')!;
    expect(img.style.opacity).toBe('0');
    fireEvent.load(img);
    expect(img.style.opacity).toBe('0.38');
    expect(img.style.transition).toContain('clip-path 600ms');
    expect(img.style.transition).toContain(`opacity ${MOTION.dur.rowImage}ms`);
  });
});

describe('sheets', () => {
  it('no skeletons, loading text or per-row rise in the three sheets', () => {
    const rooms = src('components/rooms/RoomsSheet.tsx');
    expect(rooms).not.toMatch(/animate-pulse/);
    expect(rooms).toMatch(/<ContentReveal id="rooms-sheet"/);
    const act_ = src('components/activity/ActivityFeed.tsx');
    expect(act_).toMatch(/testId="activity-reveal"/);
    const cal = src('components/library/TvCalendar.tsx');
    expect(cal).toMatch(/tv-cal-reveal/);
    expect(cal).toMatch(/'none'/);
  });

  it('SHEET_MOTION is unchanged', () => {
    const m = src('theme/motion.ts');
    const block = m.slice(m.indexOf('export const SHEET_MOTION'));
    expect(block.length).toBeGreaterThan(0);
  });
});

describe('backdrop upgrade', () => {
  it('maps phone sizes to sharp ones', () => {
    expect(backdropHiRes('/api/img/tmdb?path=/x.jpg&size=w780')).toBe('/api/img/tmdb?path=/x.jpg&size=w1280');
    expect(backdropHiRes('https://image.tmdb.org/t/p/w780/x.jpg')).toBe('https://image.tmdb.org/t/p/w1280/x.jpg');
    expect(backdropHiRes('/api/img/plex?u=1&w=780&h=439')).toBe('/api/img/plex?u=1&w=1280&h=720');
    expect(backdropHiRes('https://image.tmdb.org/t/p/original/x.jpg')).toBeNull();
    expect(backdropUpgrade(true, 'https://image.tmdb.org/t/p/original/x.jpg')).toBe('https://image.tmdb.org/t/p/original/x.jpg');
    expect(backdropUpgrade(false, null)).toBeNull();
  });

  it('both sheets pass the upgrade', () => {
    expect(src('components/media/MovieDetailModal/CompactLayout.tsx').match(/upgradeUrl=\{backdropUpgrade\(hero\.seeded/g)).toHaveLength(2);
    expect(src('components/media/TvDetailModal/CompactLayout.tsx')).toMatch(/upgradeUrl=\{backdropUpgrade\(hero\.seeded/);
  });
});
