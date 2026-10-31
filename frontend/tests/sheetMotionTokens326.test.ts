import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { MOTION } from '@/theme/motion';

describe('v326 phone sheet slide tokens (MOTION.md amendment 7:30 PM)', () => {
  it('tokens', () => {
    expect(MOTION.dur.sheetEnter).toBe(380);
    expect(MOTION.ease.sheet).toBe('cubic-bezier(0.32,0.72,0,1)');
    expect(MOTION.dur.sheetExit).toBe(260);
    expect(MOTION.ease.sheetExit).toBe('cubic-bezier(0.32,0,0.67,0)');
    expect(MOTION.dur.sheetDragMin).toBe(180);
    expect(MOTION.dur.dismissExit).toBe(300);
    const css = readFileSync('src/index.css', 'utf8');
    expect(css).toContain('--m-ease-sheet: cubic-bezier(0.32, 0.72, 0, 1)');
    expect(css).toContain('--m-ease-sheet-exit: cubic-bezier(0.32, 0, 0.67, 0)');
  });
  it('SheetFrame and the drag release read them; scrim exits over the same duration', () => {
    const s = readFileSync('src/components/layout/SheetFrame.tsx', 'utf8');
    expect(s).toContain('easing: MOTION.ease.sheet })');
    expect(s).toContain("{ duration: exitMs, easing: MOTION.ease.sheetExit, fill: 'forwards' }),\n        scrim.animate");
    expect(s.match(/duration: exitMs, easing: MOTION\.ease\.sheetExit/g)?.length).toBe(2);
    const d = readFileSync('src/hooks/useSwipeToDismiss.ts', 'utf8');
    expect(d).toContain('Math.max(MOTION.dur.sheetDragMin, MOTION.dur.sheetExit *');
    expect(d).toContain('MOTION.ease.sheet}`');
    expect(d).not.toContain('MOTION.ease.spring');
  });
});

describe('v326 UltraBlur blobs are not composited layers', () => {
  it('no will-change on the blurred blobs (a sheet slide re-blurred them every frame)', () => {
    const s = readFileSync('src/components/media/UltraBlurOverlay.tsx', 'utf8');
    expect(s).not.toMatch(/willChange: lite \? undefined : 'transform'/);
  });
});
