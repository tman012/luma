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
/** A Home rail poster on screen under a sheet opened from Home (R1/(y)). Only those keep loading: a search open holds
 *  everything (I1440/I390: on-screen posters behind a slow TMDb would hold every HTTP/1.1 socket the tap needs). */
let keepHomePosters = false;
function onScreen(img: HTMLImageElement): boolean {
  if (typeof window === 'undefined' || typeof img.getBoundingClientRect !== 'function') return false;
  if (!keepHomePosters || !img.closest('[data-testid^="card-"]') || img.closest('[data-testid="r3-card"]')) return false;
  const r = img.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
}
/** All layout reads first, then the writes: a read after each src removal forced a full relayout per image (a 4 s
 *  long task on the 1440 search page). */
function holdAll(imgs: HTMLImageElement[]): void {
  const vis = imgs.map(onScreen);
  imgs.forEach((img, i) => hold(img, vis[i] === true));
}
function hold(img: HTMLImageElement, visible: boolean): void {
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
  // v341o (Tester R1/(y)): a Home poster on screen under the sheet keeps loading: dropping its src threw its bytes
  // away, and on fast4g the restart at the reveal/close took 2.6-2.9 s (control 0.1-0.2 s).
  if (visible) return;
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
let watcher: MutationObserver | null = null;
function quietPageImages(): void {
  if (typeof document === 'undefined') return;
  holdAll(Array.from(document.querySelectorAll<HTMLImageElement>('img')));
  if (watcher || typeof MutationObserver === 'undefined' || !document.body) return;
  watcher = new MutationObserver((recs) => {
    if (!sheetOpening()) return;
    const imgs: HTMLImageElement[] = [];
    for (const r of recs) {
      if (r.type === 'attributes') { if (r.target instanceof HTMLImageElement) imgs.push(r.target); continue; }
      r.addedNodes.forEach((n) => {
        if (n instanceof HTMLImageElement) imgs.push(n);
        else if (n instanceof Element) n.querySelectorAll('img').forEach((i) => imgs.push(i as HTMLImageElement));
      });
    }
    holdAll(imgs);
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

/** Set by the tap just before beginSheetPriority: whether it came from a Home card. Consumed by that begin. */
let nextFromHome = false;
export function keepHomePostersOnOpen(fromHome: boolean): void { nextFromHome = fromHome; }

export function beginSheetPriority(): void {
  if (typeof window === 'undefined') return;
  keepHomePosters = nextFromHome; nextFromHome = false;
  opens += 1;
  until = Date.now() + SHEET_PRIORITY_MS;
  window.clearTimeout(timer);
  timer = window.setTimeout(endSheetPriority, SHEET_PRIORITY_MS);
  quietPageImages();
  // v341o (R1/(y)): Home's decodeAll posters already downloading are its first viewport (on screen under the
  // sheet): they finish; dropping them threw their bytes away. Decodes not yet started wait (pageDecode's go()).
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
