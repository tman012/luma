/** Detail sheets open finished (Taylor 11/25: "load very smoothly with no flashing or hiccups").
 *
 *  A tapped movie/TV sheet mounts at once but stays hidden (no scrim, no slide, taps pass through to the screen
 *  under it) while it loads and decodes what its first frame shows: details, backdrop, palette, logo or title,
 *  meta and buttons (its one reveal, useOneReveal). Two frames after that reveal has committed, the sheet plays
 *  its normal open motion already complete, with every reveal fade finished, so no frame shows the empty sheet.
 *  If the reveal is not in by SHEET_READY_HOLD_MS after the tap, the sheet opens anyway on a neutral dark card
 *  (never the purple sheet color) and the content fades in when it lands.
 *
 *  While held the sheet's root carries `data-sheet-held`: a tap on another title supersedes it (the host replaces
 *  it), and Back or Escape closes it without it ever showing. */
import { useEffect, useRef, useState } from 'react';
import { tapElapsed } from '@/services/modalPrefetch';

export const SHEET_READY_HOLD_MS = 1200;
export const SHEET_HELD_ATTR = 'data-sheet-held';
/** A tap older than this at mount is not the tap that opened this sheet (the sheet opens one task after its tap). */
const TAP_FRESH_MS = 400;

export interface SheetPresent { present: boolean; early: boolean }

/** How long a revealed sheet still waits for `extra` (its palette when that lands after the reveal). */
export const SHEET_EXTRA_WAIT_MS = 250;

export function useSheetPresent(ready: boolean, extra = true): SheetPresent {
  const [st, setSt] = useState<SheetPresent>({ present: false, early: false });
  const wait = useRef<number | null>(null);
  if (wait.current === null) {
    const e = tapElapsed();
    wait.current = e !== null && e < TAP_FRESH_MS ? Math.max(0, SHEET_READY_HOLD_MS - e) : SHEET_READY_HOLD_MS;
  }
  const latched = st.present;
  useEffect(() => {
    if (latched) return;
    const t = window.setTimeout(() => setSt((s) => (s.present ? s : { present: true, early: true })), wait.current ?? SHEET_READY_HOLD_MS);
    return () => window.clearTimeout(t);
  }, [latched]);
  useEffect(() => {
    if (latched || !ready) return;
    const go = () => setSt((s) => (s.present ? s : { present: true, early: false }));
    let f1 = 0; let f2 = 0;
    // Two frames after the revealed commit: its decoded images are in the hidden card's layers before the slide.
    const frames = () => {
      if (typeof requestAnimationFrame !== 'function') { go(); return; }
      f1 = requestAnimationFrame(() => { f2 = requestAnimationFrame(go); });
    };
    const t = extra ? 0 : window.setTimeout(frames, SHEET_EXTRA_WAIT_MS);
    if (extra) frames();
    return () => { window.clearTimeout(t); if (typeof cancelAnimationFrame === 'function') { cancelAnimationFrame(f1); cancelAnimationFrame(f2); } };
  }, [ready, extra, latched]);
  return st;
}

/** Finishes every finite animation and transition inside `root` (the reveal fades that ran while the sheet was
 *  held), so the open motion starts on the final pixels. Infinite ones (spinners, shimmer) are left alone. */
export function settleAnimations(root: Element | null | undefined): void {
  if (!root || typeof (root as Element & { getAnimations?: unknown }).getAnimations !== 'function') return;
  for (const a of root.getAnimations({ subtree: true })) {
    const end = a.effect?.getComputedTiming().endTime;
    if (typeof end === 'number' && Number.isFinite(end)) { try { a.finish(); } catch { /* not finishable */ } }
  }
}

/** True when a held (not yet shown) detail sheet is mounted. */
export function heldSheetMounted(): boolean {
  return typeof document !== 'undefined' && !!document.querySelector(`[${SHEET_HELD_ATTR}]`);
}
