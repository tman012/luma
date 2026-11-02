/** While a detail sheet opens (tap to its one reveal, at most SHEET_PRIORITY_MS), the sheet's art owns the network:
 *  background warm-ups wait, Home's still-loading card images drop to low priority, and offscreen ones are paused
 *  (their download dropped) and resumed after the reveal. Memory only; nothing is stored. */
export const SHEET_PRIORITY_MS = 2500;
let until = 0;
let timer = 0;
const listeners = new Set<() => void>();
const paused: { img: HTMLImageElement; src: string | null; srcset: string | null }[] = [];

const onBegin = new Set<() => void>();
/** Runs `fn` at every open's start (the image loader drops its background art there). */
export function onSheetPriority(fn: () => void): void { onBegin.add(fn); }

let opens = 0;
/** Opens so far this session (a warm-up that ran across one may have been cut short by it). */
export function sheetOpens(): number { return opens; }

export function sheetOpening(): boolean { return Date.now() < until; }
/** Runs `fn` once the current open's priority window ends (now when none). */
export function afterSheetPriority(fn: () => void): void { if (!sheetOpening()) fn(); else listeners.add(fn); }

const SHEET = '[data-modal="MovieDetailModal"],[data-modal="TvDetailModal"]';
/** A page image still loading (or not started) outside the sheet: its download is held until the reveal, so the
 *  tapped title's art goes first (v341k: on-screen rail posters too, and ones that mount or start after the tap). */
function hold(img: HTMLImageElement): void {
  if (img.complete && img.getAttribute('src')) return;
  if (img.closest(SHEET) || img.closest('[data-seed-layer]') || img.hasAttribute('data-sheet-held')) return;
  const src = img.getAttribute('src'), srcset = img.getAttribute('srcset');
  if (src == null && srcset == null) return;
  paused.push({ img, src, srcset });
  img.setAttribute('data-sheet-held', '');
  img.setAttribute('fetchpriority', 'low');
  img.removeAttribute('srcset'); img.removeAttribute('src');
}
let watcher: MutationObserver | null = null;
function quietPageImages(): void {
  if (typeof document === 'undefined') return;
  for (const img of Array.from(document.querySelectorAll<HTMLImageElement>('img'))) hold(img);
  if (watcher || typeof MutationObserver === 'undefined' || !document.body) return;
  watcher = new MutationObserver((recs) => {
    if (!sheetOpening()) return;
    for (const r of recs) {
      if (r.type === 'attributes') { if (r.target instanceof HTMLImageElement) hold(r.target); continue; }
      r.addedNodes.forEach((n) => {
        if (n instanceof HTMLImageElement) hold(n);
        else if (n instanceof Element) n.querySelectorAll('img').forEach((i) => hold(i as HTMLImageElement));
      });
    }
  });
  watcher.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['src', 'srcset'] });
}
function resume(): void {
  watcher?.disconnect(); watcher = null;
  for (const p of paused.splice(0)) {
    p.img.removeAttribute('data-sheet-held');
    // Only an image still paused is restored (a re-render that set a new src while paused wins).
    if (p.srcset != null && !p.img.hasAttribute('srcset')) p.img.setAttribute('srcset', p.srcset);
    if (p.src != null && !p.img.hasAttribute('src')) p.img.setAttribute('src', p.src);
  }
}

export function beginSheetPriority(): void {
  if (typeof window === 'undefined') return;
  opens += 1;
  until = Date.now() + SHEET_PRIORITY_MS;
  window.clearTimeout(timer);
  timer = window.setTimeout(endSheetPriority, SHEET_PRIORITY_MS);
  quietPageImages();
  for (const f of Array.from(onBegin)) { try { f(); } catch { /* its own */ } }
}
export function endSheetPriority(): void {
  if (!until) return;
  until = 0;
  if (typeof window !== 'undefined') window.clearTimeout(timer);
  resume();
  const fns = Array.from(listeners); listeners.clear();
  for (const f of fns) { try { f(); } catch { /* a listener's error stays its own */ } }
}
export function _resetSheetPriority(): void { until = 0; listeners.clear(); paused.length = 0; watcher?.disconnect(); watcher = null; }
