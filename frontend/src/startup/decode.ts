/** Load and decode each URL (an error counts as done); resolves when all are done or after *capMs*. */
import { pageDecode } from '@/loading/sheetPriority';

export function decodeAll(urls: string[], capMs: number): Promise<void> {
  if (!urls.length || typeof Image === 'undefined') return Promise.resolve();
  // Through the sheet hold (v341l): a tap cancels the ones still downloading and they start again after its reveal.
  const each = urls.map(u => pageDecode(u));
  return new Promise<void>(resolve => {
    const t = window.setTimeout(resolve, capMs);
    void Promise.all(each).then(() => { window.clearTimeout(t); resolve(); });
  });
}

/** The images of the first screen: every one in the viewport, made eager and high priority so the splash waits for
 *  exactly the pixels Home will show (and nothing loads after the handoff). */
export function firstScreenImages(root: ParentNode = document): HTMLImageElement[] {
  const vw = window.innerWidth, vh = window.innerHeight;
  return Array.from(root.querySelectorAll<HTMLImageElement>('img')).filter(i => {
    if (i.closest('#splash')) return false;
    const r = i.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw)) return false;
    i.loading = 'eager';
    i.setAttribute('fetchpriority', 'high');
    return true;
  });
}
