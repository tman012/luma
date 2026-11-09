/** Pixels a viewport-height unit leaves inside the layout viewport.
 *
 *  On some mobile browsers `100dvh` / `100lvh` end at the safe-area boundary while
 *  `position:fixed; inset:0` is the layout viewport, which continues to the
 *  screen edge. The difference is the unpainted band. It is 0 when the unit
 *  already reaches that edge, so pages do not grow an extra scroll strip.
 *  A difference past MAX_SAFE_BAND is not a safe-area band and is ignored.
 */
export const MAX_SAFE_BAND = 160;

export function screenGapPx(edgeHeight: number, unitHeight: number): number {
  if (!Number.isFinite(edgeHeight) || !Number.isFinite(unitHeight)) return 0;
  if (edgeHeight <= 0 || unitHeight <= 0) return 0;
  const gap = Math.round(edgeHeight - unitHeight);
  if (gap <= 1 || gap > MAX_SAFE_BAND) return 0;
  return gap;
}

/** Measure the layout viewport against 100dvh and against --luma-screen-h (100lvh). */
export function readScreenGaps(doc: Document = document): { flow: number; fixed: number } {
  const root = doc.documentElement;
  const hold = doc.createElement('div');
  hold.setAttribute('data-luma-gap-probe', '');
  hold.style.cssText = 'position:fixed;left:0;top:0;visibility:hidden;pointer-events:none;z-index:-1;';
  const edge = doc.createElement('div');
  edge.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;';
  const flow = doc.createElement('div');
  flow.style.cssText = 'height:100dvh;width:0;';
  const fixed = doc.createElement('div');
  fixed.style.cssText = 'height:var(--luma-screen-h, 100lvh);width:0;';
  hold.append(edge, flow, fixed);
  root.appendChild(hold);
  const edgeH = edge.getBoundingClientRect().height;
  const flowGap = screenGapPx(edgeH, flow.getBoundingClientRect().height);
  const fixedGap = screenGapPx(edgeH, fixed.getBoundingClientRect().height);
  hold.remove();
  return { flow: flowGap, fixed: fixedGap };
}

export function applyScreenGaps(doc: Document = document): { flow: number; fixed: number } {
  const gaps = readScreenGaps(doc);
  const root = doc.documentElement;
  root.style.setProperty('--luma-screen-gap', `${gaps.flow}px`);
  root.style.setProperty('--luma-fixed-gap', `${gaps.fixed}px`);
  return gaps;
}
