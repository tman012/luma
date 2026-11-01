import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { useOneReveal } from '@/hooks/useOneReveal';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
type P = { k: string; d: boolean; h: boolean; r: boolean; dl: boolean; t: boolean };
const mount = (p: P) => renderHook(({ k, d, h, r, dl, t }: P) => useOneReveal(k, d, h, r, dl, t), { initialProps: p });

describe('v341: one reveal for the whole sheet', () => {
  it('opens only with the details, the latched hero and the ratings (or the deadline), on the same render', () => {
    const h = mount({ k: 'a', d: true, h: false, r: true, dl: false, t: false });
    expect(h.result.current.revealed).toBe(false);          // details in, hero not latched: nothing shows
    h.rerender({ k: 'a', d: true, h: true, r: false, dl: false, t: false });
    expect(h.result.current.revealed).toBe(false);          // hero latched, ratings out, before the deadline
    h.rerender({ k: 'a', d: true, h: true, r: false, dl: true, t: false });
    expect(h.result.current.revealed).toBe(true);           // the deadline releases everything together
    h.rerender({ k: 'a', d: true, h: true, r: true, dl: true, t: false });
    expect(h.result.current.revealed).toBe(true);           // latched for the open
    h.rerender({ k: 'b', d: false, h: false, r: false, dl: false, t: false });
    expect(h.result.current.revealed).toBe(false);          // a new open starts over
  });

  it('the trailer button is decided at the reveal: a late trailer is left out, so the action row x positions never change', () => {
    const h = mount({ k: 'a', d: true, h: true, r: true, dl: false, t: false });
    expect(h.result.current).toEqual({ revealed: true, trailer: false });
    h.rerender({ k: 'a', d: true, h: true, r: true, dl: false, t: true });
    expect(h.result.current.trailer).toBe(false);
    const g = mount({ k: 'a', d: true, h: false, r: true, dl: false, t: true });
    g.rerender({ k: 'a', d: true, h: true, r: true, dl: false, t: true });
    expect(g.result.current).toEqual({ revealed: true, trailer: true });
    const m = src('components/media/MovieDetailModal/CompactLayout.tsx');
    expect(m).toMatch(/\(trailerKey && reveal\.trailer \? 1 : 0\)/);
    expect(m).toMatch(/\{trailerKey && reveal\.trailer && \(/);
    const e = src('components/media/MovieDetailModal/ExpandedLayout.tsx');
    expect(e).toMatch(/useOneReveal\(props\.openKey, !!detail, true, true, true, !!trailerKey\)\.trailer/);
    expect(e).toMatch(/\{trailerKey && actionsTrailer && \(/);
  });

  it('every header and body element sits behind the one gate and shares the one fade', () => {
    for (const k of ['MovieDetailModal', 'TvDetailModal']) {
      const c = src(`components/media/${k}/CompactLayout.tsx`);
      expect(c).toMatch(/useOneReveal\(props\.openKey, !loading && !error && !!(detail|show), sheetHero\.state !== 'hold', ratingsSettle\.shown,\s*sheetHero\.deadline/);
      // title/logo
      expect(c).toMatch(/opacity: sheetHero\.state === 'hold' \|\| !reveal\.revealed \? 0 : 1, transition: reducedMotion\(\) \? 'none' : HERO_FADE/);
      // backdrop
      expect(c).not.toMatch(/hold=\{heroHold\} instant\n/);
      expect(c).toMatch(/hold=\{!reveal\.revealed\} instant/);
      // meta, ratings, buttons, overview: the details container
      expect(c).toMatch(/data-reveal-gate=\{reveal\.revealed \? 'shown' : 'hold'\}/);
      expect(c).not.toMatch(/data-details-fade=/);
    }
    const css = src('index.css');
    expect(css).toMatch(/\[data-reveal-gate\] > :not\(\[data-hero-keep\]\)\{transition:opacity 0\.45s ease-out\}/);
    expect(css).toMatch(/\[data-reveal-gate="hold"\] > :not\(\[data-hero-keep\]\)\{opacity:0\}/);
    expect(src('hooks/useSheetBackdrop.ts')).toMatch(/HERO_FADE = 'opacity 0\.45s ease-out'/);
  });
});
