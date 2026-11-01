/** While a detail sheet opens (tap to its one reveal, at most SHEET_PRIORITY_MS), the sheet's art owns the network:
 *  background warm-ups wait, Home's still-loading card images drop to low priority, and offscreen ones are paused
 *  (their download dropped) and resumed after the reveal. Memory only; nothing is stored. */
export const SHEET_PRIORITY_MS = 2500;
let until = 0;
let timer = 0;
const listeners = new Set<() => void>();
const paused: { img: HTMLImageElement; src: string | null; srcset: string | null }[] = [];

export function sheetOpening(): boolean { return Date.now() < until; }
/** Runs `fn` once the current open's priority window ends (now when none). */
export function afterSheetPriority(fn: () => void): void { if (!sheetOpening()) fn(); else listeners.add(fn); }

const SHEET = '[data-modal="MovieDetailModal"],[data-modal="TvDetailModal"]';
function quietPageImages(): void {
  if (typeof document === 'undefined') return;
  const vw = window.innerWidth, vh = window.innerHeight;
  for (const img of Array.from(document.querySelectorAll<HTMLImageElement>('img'))) {
    if (img.complete || img.closest(SHEET) || img.closest('[data-seed-layer]')) continue;
    const r = img.getBoundingClientRect();
    const on = r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw && r.width > 0;
    if (on) { img.setAttribute('fetchpriority', 'low'); continue; }
    paused.push({ img, src: img.getAttribute('src'), srcset: img.getAttribute('srcset') });
    img.removeAttribute('srcset'); img.removeAttribute('src');
  }
}
function resume(): void {
  for (const p of paused.splice(0)) {
    // Only an image still paused is restored (a re-render that set a new src while paused wins).
    if (p.srcset != null && !p.img.hasAttribute('srcset')) p.img.setAttribute('srcset', p.srcset);
    if (p.src != null && !p.img.hasAttribute('src')) p.img.setAttribute('src', p.src);
  }
}

export function beginSheetPriority(): void {
  if (typeof window === 'undefined') return;
  until = Date.now() + SHEET_PRIORITY_MS;
  window.clearTimeout(timer);
  timer = window.setTimeout(endSheetPriority, SHEET_PRIORITY_MS);
  quietPageImages();
}
export function endSheetPriority(): void {
  if (!until) return;
  until = 0;
  if (typeof window !== 'undefined') window.clearTimeout(timer);
  resume();
  const fns = Array.from(listeners); listeners.clear();
  for (const f of fns) { try { f(); } catch { /* a listener's error stays its own */ } }
}
export function _resetSheetPriority(): void { until = 0; listeners.clear(); paused.length = 0; }
