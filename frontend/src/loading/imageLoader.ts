/** The app's image loader: one fetch and one decode per URL, the decoded image kept in memory (an LRU of
 *  IMAGE_LOADER_MAX entries) so every surface paints it at once. Memory only: nothing is stored on the device. */
export const IMAGE_LOADER_MAX = 150;

interface Entry { img: HTMLImageElement; ready: Promise<void>; done: boolean }
const entries = new Map<string, Entry>();

function touch(url: string, e: Entry): void {
  entries.delete(url);
  entries.set(url, e);
  while (entries.size > IMAGE_LOADER_MAX) entries.delete(entries.keys().next().value as string);
}

/** Loads and decodes `url`; resolves when it is ready to paint, or has failed (a failure is forgotten, so the
 *  next request retries). */
export function loadImage(url: string | null | undefined): Promise<void> {
  if (!url || typeof Image === 'undefined') return Promise.resolve();
  const hit = entries.get(url);
  if (hit) { touch(url, hit); return hit.ready; }
  const img = new Image();
  img.decoding = 'async';
  const entry: Entry = { img, ready: Promise.resolve(), done: false };
  img.src = url;
  // decode() waits for the load itself and rejects on a broken image; without it, the load event.
  entry.ready = (typeof img.decode === 'function'
    ? img.decode().then(() => true, () => false)
    : new Promise<boolean>((res) => { img.onload = () => res(true); img.onerror = () => res(false); })
  ).then((ok) => { entry.done = ok; if (!ok) entries.delete(url); });
  touch(url, entry);
  return entry.ready;
}

/** True once `url` is decoded and held in memory. */
export function isDecoded(url: string | null | undefined): boolean {
  return !!url && !!entries.get(url)?.done;
}

export function _resetImageLoader(): void {
  entries.clear();
}
