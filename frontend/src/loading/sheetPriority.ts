/** While a detail sheet opens (tap to its one reveal, at most SHEET_PRIORITY_MS), the sheet's art owns the network:
 *  background warm-ups wait, Home's still-loading card images drop to low priority, and offscreen ones are paused
 *  (their download dropped) and resumed after the reveal. Memory only; nothing is stored. */
/** A safety cap only: the window ends at the sheet's reveal (useOneReveal), or when it closes unrevealed (v341l:
 *  2.5 s let warm-ups resume before a slow-TMDb reveal). */
export const SHEET_PRIORITY_MS = 6000;
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

/** A page image still loading (or not started) outside the sheet: its download is held until the reveal, so the
 *  tapped title's art goes first (v341k: on-screen rail posters too, and ones that mount or start after the tap). */
function hold(img: HTMLImageElement): void {
  // Only the page under the sheet: never a sheet, dialog or panel over it (search palette, person sheet; v341l F1).
  if (img.closest('[data-modal]') || img.closest('[role="dialog"]') || img.closest('[data-seed-layer]')) return;
  const src = img.getAttribute('src'), srcset = img.getAttribute('srcset');
  if (img.hasAttribute('data-sheet-held')) {
    // A re-render set a new src while held (v341l F2): it's the one restored at the reveal, not loaded now.
    if (src == null && srcset == null) return;
    const p = paused.find((x) => x.img === img);
    if (p) { if (src != null) p.src = src; if (srcset != null) p.srcset = srcset; }
    img.removeAttribute('srcset'); img.removeAttribute('src');
    return;
  }
  if (img.complete && src) return;
  if (src == null && srcset == null) return;
  paused.push({ img, src, srcset });
  img.setAttribute('data-sheet-held', '');
  img.setAttribute('fetchpriority', 'low');
  img.removeAttribute('srcset'); img.removeAttribute('src');
}
/** Off-page decodes of page images (Home's first-viewport posters, decodeAll): an open cancels the ones still
 *  downloading (src dropped, so the browser frees the connection) and starts them again after the reveal (v341l:
 *  on HTTP/1.1, in-flight posters held all 6 connections for 2.4-3.9 s). */
const offPage = new Set<{ im: HTMLImageElement; cut: boolean }>();
export function pageDecode(url: string): Promise<void> {
  if (typeof Image === 'undefined') return Promise.resolve();
  return new Promise<void>((done) => {
    const go = () => {
      if (sheetOpening()) { afterSheetPriority(go); return; }
      const im = new Image();
      im.decoding = 'async';
      const rec = { im, cut: false };
      offPage.add(rec);
      const fin = () => { offPage.delete(rec); if (rec.cut) afterSheetPriority(go); else done(); };
      im.src = url;
      // Without decode() (old engines, tests) nothing waits on it, as before.
      if (typeof im.decode === 'function') im.decode().then(fin, fin); else fin();
    };
    go();
  });
}
function cutOffPage(): void {
  for (const r of Array.from(offPage)) {
    if (r.im.complete) continue;
    r.cut = true;
    // decode() rejects on the dropped src; its handler restarts the load after the reveal.
    r.im.removeAttribute('src');
  }
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
  cutOffPage();
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
export function _resetSheetPriority(): void { until = 0; listeners.clear(); paused.length = 0; offPage.clear(); watcher?.disconnect(); watcher = null; }
