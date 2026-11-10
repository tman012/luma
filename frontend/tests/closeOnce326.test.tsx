import { describe, expect, it, vi, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { ENTER_FALLBACK_SLACK_MS, useCloseOnce } from '@/components/layout/SheetFrame';
import { MOTION } from '@/theme/motion';

afterEach(() => vi.useRealTimers());

describe('v326 one close per sheet (Back + close button within 300 ms)', () => {
  it('only the first close within MOTION.dur.dismissExit runs', () => {
    let now = 1000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const { result } = renderHook(() => useCloseOnce());
    expect(result.current()).toBe(true);    // Back
    now += 120;
    expect(result.current()).toBe(false);   // close button 120 ms later: no second history step
    now += MOTION.dur.dismissExit;
    expect(result.current()).toBe(true);
  });
  it('the shared lifecycle guards the close button and the Back dismiss with the same closeOnce', () => {
    const s = readFileSync('src/components/motion/useSurfacePresence.ts', 'utf8');
    expect(s).toContain('!closeOnce()) return;');
    expect(s.match(/!closeOnce\(\)/g)?.length).toBe(2);
  });
  it.each(['src/components/media/MovieDetailModal/index.tsx', 'src/components/media/TvDetailModal/index.tsx', 'src/components/media/PersonDetailModal.tsx'])(
    '%s uses the shared lifecycle instead of its own timer', (p) => {
      const s = readFileSync(p, 'utf8');
      expect(s).toContain('useSurfacePresence(');
      expect(s).not.toMatch(/setTimeout\(\(\) => \{\s*setPhase\('gone'\)/);
    });
  it('DISMISS_EXIT_MS is the motion token; the enter has a fallback; the page paint is a scoped selector', () => {
    expect(MOTION.dur.dismissExit).toBe(340);
    expect(readFileSync('src/contexts/AppModalHost.tsx', 'utf8')).toContain('const DISMISS_EXIT_MS = MOTION.dur.dismissExit;');
    expect(ENTER_FALLBACK_SLACK_MS).toBe(200);
    const sf = readFileSync('src/components/layout/SheetFrame.tsx', 'utf8');
    expect(sf).not.toContain('documentElement.style.background');
    expect(readFileSync('src/index.css', 'utf8')).toMatch(/html\[data-luma-sheet\],\nhtml\[data-luma-sheet\] body,/);
  });
});
