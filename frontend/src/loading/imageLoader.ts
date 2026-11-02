/** The app's image loader: one fetch and one decode per URL, the decoded image kept in memory (an LRU of
 *  IMAGE_LOADER_MAX entries) so every surface paints it at once. Memory only: nothing is stored on the device. */
export const IMAGE_LOADER_MAX = 150;

import { afterSheetPriority, onSheetPriority, sheetOpening } from './sheetPriority';

/** Background (low priority) art: at most LOW_CAP downloads at once, none while a sheet opens; an open drops the
 *  ones in flight (requeued), so the tapped title's art never waits behind a warm-up. */
export const LOW_CAP = 2;

interface Entry { img: HTMLImageElement; ready: Promise<void>; done: boolean }
interface Low { url: string; start: (p: 'high' | 'low') => void; img?: HTMLImageElement }
const lowQueue: Low[] = [];
const lowActive = new Set<Low>();
const lowByUrl = new Map<string, Low>();

function pumpLow(): void {
  if (sheetOpening()) { afterSheetPriority(pumpLow); return; }
  while (lowActive.size < LOW_CAP && lowQueue.length) {
    const l = lowQueue.shift()!;
    lowActive.add(l);
    l.start('low');
  }
}
onSheetPriority(() => {
  for (const l of Array.from(lowActive).reverse()) {
    lowActive.delete(l);
    // Dropping src cancels the download; the warm-up goes back to the queue's front.
    if (l.img) { l.img.onload = null; l.img.removeAttribute('src'); l.img = undefined; }
    lowQueue.unshift(l);
  }
  afterSheetPriority(pumpLow);
});
const entries = new Map<string, Entry>();

function touch(url: string, e: Entry): void {
  entries.delete(url);
  entries.set(url, e);
  while (entries.size > IMAGE_LOADER_MAX) entries.delete(entries.keys().next().value as string);
}

/** Loads and decodes `url`; resolves when it is ready to paint, or has failed (a failure is forgotten, so the
 *  next request retries). */
export function loadImage(url: string | null | undefined, priority?: 'high' | 'low'): Promise<void> {
  if (!url || typeof Image === 'undefined') return Promise.resolve();
  const hit = entries.get(url);
  if (hit) {
    touch(url, hit);
    // A queued or in-flight warm-up asked for now (the tap): it starts at once, at high priority.
    const l = lowByUrl.get(url);
    if (l && priority !== 'low') { const q = lowQueue.indexOf(l); if (q >= 0) lowQueue.splice(q, 1); lowActive.delete(l); lowByUrl.delete(url); l.start('high'); pumpLow(); }
    return hit.ready;
  }
  const entry: Entry = { img: new Image(), ready: Promise.resolve(), done: false };
  let settle: (ok: boolean) => void = () => {};
  const settled = new Promise<boolean>((res) => { settle = res; });
  const start = (p: 'high' | 'low') => {
    const img = new Image();
    entry.img = img;
    img.decoding = 'async';
    (img as HTMLImageElement & { fetchPriority?: string }).fetchPriority = p;
    img.src = url;
    const l = lowByUrl.get(url);
    if (l) l.img = img;
    // decode() waits for the load itself and rejects on a broken image; without it, the load event.
    (typeof img.decode === 'function'
      ? img.decode().then(() => true, () => false)
      : new Promise<boolean>((res) => { img.onload = () => res(true); img.onerror = () => res(false); })
    ).then((ok) => {
      if (entry.img !== img) return; // dropped by an open (or restarted at high): its own result is the one
      const w = lowByUrl.get(url);
      if (w) { lowByUrl.delete(url); lowActive.delete(w); pumpLow(); }
      settle(ok);
    });
  };
  entry.ready = settled.then((ok) => { entry.done = ok; if (!ok && entries.get(url) === entry) entries.delete(url); });
  touch(url, entry);
  if (priority === 'low') {
    const l: Low = { url, start };
    lowByUrl.set(url, l);
    lowQueue.push(l);
    pumpLow();
  } else start(priority ?? 'auto' as 'high');
  return entry.ready;
}

/** True once `url` is decoded and held in memory. */
export function isDecoded(url: string | null | undefined): boolean {
  return !!url && !!entries.get(url)?.done;
}

export function _resetImageLoader(): void {
  entries.clear(); lowQueue.length = 0; lowActive.clear(); lowByUrl.clear();
}
