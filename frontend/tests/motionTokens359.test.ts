/** Phase 1 motion system: the detail sheets and their nested sheets take every duration and easing from the shared
 *  tokens (theme/motion.ts, written to CSS by motionCss), never a literal; nothing overshoots; reduced motion
 *  shortens the tokens. */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  exitMs, MODAL_MOTION, MOTION, motionCss, REVEAL_MOTION, REVEAL_TRANSITION, SHEET_MOTION, STATE_MOTION,
} from '@/theme/motion';

const SRC = join(__dirname, '../src');
const read = (p: string) => readFileSync(join(SRC, p), 'utf8');
const dirFiles = (d: string) => readdirSync(join(SRC, d)).filter((f) => f.endsWith('.tsx') || f.endsWith('.ts')).map((f) => `${d}/${f}`);

/** The Phase 1 surface: both detail sheets and everything nested in them, the shared sheet frame and the hooks that
 *  time the reveal. */
const SCOPE = [
  'components/layout/SheetFrame.tsx',
  'components/motion/useSurfacePresence.ts',
  'components/media/BackdropHero.tsx',
  'components/media/UltraBlurOverlay.tsx',
  'components/media/PersonDetailModal.tsx',
  'hooks/useSheetBackdrop.ts',
  'hooks/useOneReveal.ts',
  ...dirFiles('components/media/MovieDetailModal'),
  ...dirFiles('components/media/TvDetailModal'),
];

/** A literal duration or easing inside a transition or animation value (a spinner's `linear infinite` is a loop, not a
 *  motion). */
const LITERAL = /(?:\b\d*\.?\d+m?s\b|\bcubic-bezier\(|\bease(?:-in|-out|-in-out)?\b)/;
function literalTimings(src: string): string[] {
  const hits: string[] = [];
  src.split('\n').forEach((line, i) => {
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) return;
    const m = /\b(?:transition|animation)[A-Za-z]*['"]?\s*[:=]\s*(.*)$/.exec(line);
    if (!m) return;
    const value = m[1]!;
    if (/infinite/.test(value)) return;
    const strings = value.match(/(['"`])(?:(?!\1).)*\1/g) ?? [];
    for (const lit of strings) {
      const inner = lit.slice(1, -1).replace(/var\([^)]*\)/g, '').replace(/\$\{[^}]*\}/g, '');
      if (LITERAL.test(inner)) hits.push(`${i + 1}: ${line.trim().slice(0, 140)}`);
    }
  });
  return hits;
}

describe('Phase 1 motion tokens', () => {
  it.each(SCOPE)('%s has no literal duration or easing', (f) => {
    expect(literalTimings(read(f))).toEqual([]);
  });

  it('the scanner sees a literal (so the check above can fail)', () => {
    expect(literalTimings("style={{ transition: 'opacity 0.3s ease' }}")).toHaveLength(1);
    expect(literalTimings("style={{ animation: 'x 0.2s cubic-bezier(0,0,1,1)' }}")).toHaveLength(1);
    expect(literalTimings("style={{ transition: 'opacity var(--m-dur-reveal) var(--m-ease-reveal)' }}")).toHaveLength(0);
    expect(literalTimings("style={{ animation: 'spin 0.8s linear infinite' }}")).toHaveLength(0);
  });

  it('no easing in the scope or the tokens overshoots (no bounce)', () => {
    const curves = [SHEET_MOTION.enterEase, SHEET_MOTION.exitEase, SHEET_MOTION.scrimEnterEase, SHEET_MOTION.scrimExitEase,
      MODAL_MOTION.enterEase, MODAL_MOTION.exitEase, REVEAL_MOTION.ease, MOTION.ease.standard, MOTION.ease.decel, MOTION.ease.accel];
    for (const c of curves) {
      const m = /cubic-bezier\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)/.exec(c)!;
      for (const y of [Number(m[2]), Number(m[4])]) { expect(y).toBeGreaterThanOrEqual(0); expect(y).toBeLessThanOrEqual(1); }
    }
    for (const f of SCOPE) expect(read(f), f).not.toMatch(/cubic-bezier\([^)]*,\s*1\.\d+/);
    expect(read('components/media/MovieDetailModal/SimilarPanel.tsx')).not.toContain('castShelfBounce 0.55s');
  });

  it('motion stays transform and opacity: the enter and exit animate nothing else', () => {
    const frame = read('components/layout/SheetFrame.tsx');
    const animated = [...frame.matchAll(/\.animate\(\s*(\[[\s\S]*?\])\s*,/g)].map((m) => m[1]!);
    expect(animated.length).toBeGreaterThan(4);
    for (const keyframes of animated) expect(keyframes).not.toMatch(/\b(width|height|top|left|right|bottom|margin|padding|filter)\s*:/);
  });

  it('reveal, state and modal tokens are the one definition the CSS twins are written from', () => {
    const css = motionCss();
    for (const v of [
      `--m-dur-reveal:${REVEAL_MOTION.ms}ms`, `--m-ease-reveal:${REVEAL_MOTION.ease}`,
      `--m-dur-modal-enter:${MODAL_MOTION.enterMs}ms`, `--m-dur-modal-exit:${MODAL_MOTION.exitMs}ms`,
      `--m-ease-modal-enter:${MODAL_MOTION.enterEase}`, `--m-ease-modal-exit:${MODAL_MOTION.exitEase}`,
      `--m-dur-hover:${STATE_MOTION.hoverMs}ms`, `--m-dur-selected:${STATE_MOTION.selectedMs}ms`,
      `--m-sheet-enter:${SHEET_MOTION.enterMs}ms`,
    ]) expect(css).toContain(v);
    expect(MOTION.dur.reveal).toBe(REVEAL_MOTION.ms);
    expect(MOTION.dur.press).toBe(STATE_MOTION.pressMs);
    expect(MOTION.pressScale).toBe(STATE_MOTION.pressScale);
    expect(REVEAL_TRANSITION).toBe('opacity var(--m-dur-reveal) var(--m-ease-reveal)');
    const index = read('index.css');
    expect(index).not.toMatch(/--m-dur-(reveal|modal-enter|modal-exit|hover|selected)\s*:/);
    expect(index).toContain('transition:opacity var(--m-dur-reveal) var(--m-ease-reveal)');
  });

  it('every surface reads its exit length from one function, not a literal', () => {
    expect(exitMs('sheet')).toBe(SHEET_MOTION.exitMs);
    expect(exitMs('modal')).toBe(MODAL_MOTION.exitMs);
    for (const f of ['components/media/MovieDetailModal/index.tsx', 'components/media/TvDetailModal/index.tsx', 'components/media/PersonDetailModal.tsx']) {
      expect(read(f), f).not.toMatch(/\b(200|300)\b[^\n]*\n[^\n]*onClose|setTimeout\([^)]*,\s*(200|300)\)/);
    }
    expect(read('components/motion/useSurfacePresence.ts')).toContain('exitMs(kind)');
  });
});

describe('Phase 1 reduced motion', () => {
  it('the CSS twins shorten every move to a quick fade', () => {
    const css = motionCss();
    const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'));
    expect(reduced).toContain(`--m-sheet-enter:${MOTION.dur.fast}ms`);
    expect(reduced).toContain(`--m-sheet-exit:${MOTION.dur.fast}ms`);
    expect(reduced).toContain(`--m-dur-modal-enter:${MOTION.dur.fast}ms`);
    expect(reduced).toContain(`--m-dur-modal-exit:${MOTION.dur.fast}ms`);
    expect(reduced).toContain(`--m-dur-reveal:${MOTION.dur.fast}ms`);
    expect(reduced).toContain('--m-dur-hover:0ms');
  });

  it('a pressed control does not scale under reduced motion', () => {
    const css = read('index.css');
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\{\.luma-pressable\[data-pressed="true"\],\.m-interactive\[data-pressed="true"\],\.m-interactive:active\{transform:none;opacity:\.8\}\}/);
    expect(css).toMatch(/--m-press-scale: 1;/);
  });
});

describe('Phase 1 shared states', () => {
  it('press, hover, focus and selected come from one set of classes that read the state tokens', () => {
    const css = read('index.css');
    expect(css).toContain('.luma-pressable,.m-interactive{transition:transform var(--m-dur-press) var(--m-ease-standard),opacity var(--m-dur-hover) var(--m-ease-standard)}');
    expect(css).toContain('scale(var(--m-press-scale))');
    expect(css).toContain('@media (hover:hover){.m-interactive:hover{');
    expect(css).toContain('.m-interactive:focus-visible{');
    expect(css).toContain('.m-interactive[aria-selected="true"]');
    for (const f of ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/CompactLayout.tsx']) {
      expect(read(f), f).toContain('className="m-interactive"');
    }
  });
});
