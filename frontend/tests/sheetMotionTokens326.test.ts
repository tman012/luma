import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { MOTION, SHEET_MOTION, sheetMotionCss } from '@/theme/motion';

describe('v326 phone sheet slide tokens (MOTION.md amendment 7:30 PM)', () => {
  it('tokens (Designer, MOTION.md amendment 11:32 PM ET) come from SHEET_MOTION only', () => {
    expect(SHEET_MOTION).toEqual({ enterMs: 560, enterEase: 'cubic-bezier(0.22,0.61,0.36,1)', scrimEnterMs: 560,
      scrimEnterEase: 'cubic-bezier(0.4,0,0.2,1)', exitMs: 340, exitEase: 'cubic-bezier(0.4,0,1,1)', scrimExitMs: 340,
      scrimExitEase: 'cubic-bezier(0.4,0,1,1)', dragMinMs: 220 });
    expect(MOTION.dur.sheetEnter).toBe(SHEET_MOTION.enterMs);
    expect(MOTION.ease.sheet).toBe(SHEET_MOTION.enterEase);
    expect(MOTION.dur.sheetExit).toBe(SHEET_MOTION.exitMs);
    expect(MOTION.ease.sheetExit).toBe(SHEET_MOTION.exitEase);
    expect(MOTION.dur.sheetDragMin).toBe(SHEET_MOTION.dragMinMs);
    expect(MOTION.dur.dismissExit).toBe(340);
    const css = readFileSync('src/index.css', 'utf8');
    expect(css).not.toMatch(/--m-(dur-)?sheet-(enter|exit)\s*:|--m-ease-sheet(-exit)?\s*:|--m-dur-sheet-drag-min\s*:/);
    expect(sheetMotionCss()).toContain('--m-sheet-enter:560ms;');
    expect(sheetMotionCss()).toContain('--m-ease-sheet-exit:cubic-bezier(0.4,0,1,1);');
    expect(document.getElementById('luma-sheet-motion')?.textContent).toBe(sheetMotionCss());
  });
  it('SheetFrame and the drag release read them; the scrim uses its own tokens', () => {
    const s = readFileSync('src/components/layout/SheetFrame.tsx', 'utf8');
    expect(s).toContain('easing: MOTION.ease.sheet })');
    expect(s).toContain('MOTION.dur.sheetScrimEnter');
    expect(s).toContain('MOTION.ease.sheetScrimExit');
    expect(s).not.toMatch(/cubic-bezier|\b(380|260|560|340)\b/);
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
