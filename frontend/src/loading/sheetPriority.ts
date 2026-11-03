/** While a detail sheet opens (tap to its one reveal, at most SHEET_PRIORITY_MS), the sheet's art owns the network:
 *  background warm-ups wait, Home's still-loading card images drop to low priority, and offscreen ones are paused
 *  (their download dropped) and resumed after the reveal. Memory only; nothing is stored. */
/** A safety cap only: the window ends at the sheet's reveal (useOneReveal), or when it closes unrevealed (v341l:
 *  2.5 s let warm-ups resume before a slow-TMDb reveal). */
export const SHEET_PRIORITY_MS = 6000;
let until = 0;
let timer = 0;
const listeners = new Set<() => void>();
/** A tap from a Home card: its on-screen posters are never stripped (R1/y). A search open still holds them: behind a
 *  slow TMDb the search grid's on-screen posters held all six HTTP/1.1 sockets (I1440: 4.35 s, no art). */
let keepOnScreen = false;
export const SEARCH_SURFACE = '[data-testid="search-shelf"],[data-testid="search-result"],[data-testid="search-results"],[data-testid="phone-search-row"],[data-testid="r3-card"]';
let nextFromHome = false;
export function keepHomePostersOnOpen(fromHome: boolean): void { nextFromHome = fromHome; }
/** On-screen page images lowered for the open (their own fetchpriority, restored at the reveal or Close). */
const lowered: { img: HTMLImageElement; prio: string | null }[] = [];
type Paused = { img: HTMLImageElement; src: string | null; srcset: string | null; prio: string | null; vis: boolean };
const paused: Paused[] = [];

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
function onScreen(img: HTMLImageElement): boolean {
  if (typeof window === 'undefined' || typeof img.getBoundingClientRect !== 'function') return false;
  const r = img.getBoundingClientRect();
  return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
}
/** All layout reads first, then the writes (a read after each src removal forced a relayout per image). Whether an
 *  image is on screen is read here, as the user sees the page: at Close the sheet's scroll lock has moved it. */
function holdAll(imgs: HTMLImageElement[]): void {
  const vis = imgs.map(onScreen);
  imgs.forEach((img, i) => hold(img, vis[i] === true));
}
/** A decoded image is never touched (it stays mounted and shows at once on Close). An on-screen one still loading is
 *  never stripped or restarted either (v341r, Tester real WXY on 411fe470: restarted posters came back 0.5-0.75 s
 *  after Close, 0.9-1.0 s after the reveal); it only drops to fetchpriority=low while the sheet's art loads at high.
 *  Off-screen ones (lazy margin) are held until the reveal. */
function hold(img: HTMLImageElement, vis: boolean): void {
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
  // A search surface's posters are always held (Tester PREP 46d2e5ee: phone SearchShelf posters kept all six
  // HTTP/1.1 sockets for 4.3 s behind a slow TMDb; 70fb71e1 cancelled them).
  if (vis && keepOnScreen && !img.closest(SEARCH_SURFACE)) {
    if (!lowered.some((l) => l.img === img)) { lowered.push({ img, prio: img.getAttribute('fetchpriority') }); img.setAttribute('fetchpriority', 'low'); }
    return;
  }
  paused.push({ img, src, srcset, prio: img.getAttribute('fetchpriority'), vis });
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
function restore(p: Paused): void {
  p.img.removeAttribute('data-sheet-held');
  // Its own priority back (held images were low; left low, the on-screen posters queued behind the margin's: 3.7 s).
  if (p.prio == null) p.img.removeAttribute('fetchpriority'); else p.img.setAttribute('fetchpriority', p.prio);
  // Only an image still paused is restored (a re-render that set a new src while paused wins).
  if (p.srcset != null && !p.img.hasAttribute('srcset')) p.img.setAttribute('srcset', p.srcset);
  if (p.src != null && !p.img.hasAttribute('src')) p.img.setAttribute('src', p.src);
}
/** On-screen images first (the posters Close shows); the rest, and the work queued behind the sheet (warm-ups,
 *  Home decodes), once those load or after RESUME_REST_MS: all at once, the viewport's posters shared six sockets with
 *  every lazy image in the margin and the queued warm-ups (y: 1.05 s after Close). */
const RESUME_REST_MS = 400;
/** Each hold-end's own release timer (Reviewer F1: one shared timer let an overlapping hold-end cancel another's). */
const restTimers = new Set<number>();
function resume(then: () => void): void {
  watcher?.disconnect(); watcher = null;
  const all = paused.splice(0);
  const low = lowered.splice(0);
  for (const l of low) { if (l.prio == null) l.img.removeAttribute('fetchpriority'); else l.img.setAttribute('fetchpriority', l.prio); }
  const first = all.filter((p) => p.vis), rest = all.filter((p) => !p.vis);
  first.forEach(restore);
  const wait = [...first.map((p) => p.img), ...low.map((l) => l.img)].filter((i) => !i.complete);
  let left = wait.length, done = false;
  let timer = 0;
  const go = () => { if (done) return; done = true; if (typeof window !== 'undefined') { window.clearTimeout(timer); restTimers.delete(timer); } rest.forEach(restore); then(); };
  if (!left || typeof window === 'undefined') { go(); return; }
  timer = window.setTimeout(go, RESUME_REST_MS); restTimers.add(timer);
  const one = () => { left -= 1; if (left <= 0) go(); };
  for (const i of wait) {
    i.addEventListener('load', one, { once: true });
    i.addEventListener('error', one, { once: true });
  }
}

export function beginSheetPriority(): void {
  if (typeof window === 'undefined') return;
  keepOnScreen = nextFromHome; nextFromHome = false;
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
  const fns = Array.from(listeners); listeners.clear();
  resume(() => { for (const f of fns) { try { f(); } catch { /* a listener's error stays its own */ } } });
}
export function _resetSheetPriority(): void { if (typeof window !== 'undefined') restTimers.forEach((t) => window.clearTimeout(t)); restTimers.clear(); until = 0; listeners.clear(); paused.length = 0; lowered.length = 0; offPage.clear(); watcher?.disconnect(); watcher = null; }
