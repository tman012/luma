/** v179: an open tab picks up a new build.
 *
 * Why: the app only looked for a new service worker on a page load, and a plain-HTTP LAN install
 * (http://<ip>:5002) has no service worker at all, so a tab opened before a deploy kept running the
 * old bundle for hours while its quiet calendar re-asks read the new API. That is how a v177 tab showed
 * v178's calendar data (no stills) in v177's reserved 16:9 still boxes: blank space above every card.
 *
 * How: on focus, on becoming visible and every BUILD_CHECK_MS while visible, read the app page (no-store)
 * and compare its entry bundle (/assets/index-<hash>.js) with the one this tab runs. A different one
 * reloads the tab once (never while a field has focus; never twice for the same build, so no loop). */
export const BUILD_CHECK_MS = 5 * 60_000;
/** Focus and visibility events closer together than this make one check. */
export const BUILD_FOCUS_GAP_MS = 30_000;
const ENTRY = /\/assets\/index-[A-Za-z0-9_-]+\.js/;
const SEEN_KEY = 'luma-build-reloaded-for';

/** The entry bundle path a page's HTML loads, or null. */
export function entryOf(html: string): string | null {
  return html.match(ENTRY)?.[0] ?? null;
}

/** The entry bundle this tab runs, or null (the Vite dev server has none). */
export function runningEntry(doc: Document = document): string | null {
  for (const s of Array.from(doc.querySelectorAll<HTMLScriptElement>('script[type="module"][src]'))) {
    const hit = entryOf(s.getAttribute('src') || '');
    if (hit) return hit;
  }
  return null;
}

const editing = (doc: Document) => {
  const a = doc.activeElement as HTMLElement | null;
  return !!a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT' || a.isContentEditable);
};

export type BuildCheck = 'same' | 'reloaded' | 'deferred' | 'skipped';
export interface BuildWatchDeps {
  base: string;
  fetchImpl?: typeof fetch;
  doc?: Document;
  reload?: () => void;
  storage?: Pick<Storage, 'getItem' | 'setItem'>;
}

export async function checkBuild({ base, fetchImpl = fetch, doc = document, reload = () => window.location.reload(),
  storage = window.sessionStorage }: BuildWatchDeps): Promise<BuildCheck> {
  const mine = runningEntry(doc);
  if (!mine) return 'skipped';
  let html: string;
  try {
    const r = await fetchImpl(`${base}/`, { cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'text/html' } });
    if (!r.ok || r.headers.get('X-Luma-App-Shell') !== '1') return 'skipped';   // only the real app page counts
    html = await r.text();
  } catch {
    return 'skipped';                                                            // offline: keep what runs
  }
  const live = entryOf(html);
  if (!live || live === mine) return live ? 'same' : 'skipped';
  if (editing(doc)) return 'deferred';                                           // the next check tries again
  try {
    if (storage.getItem(SEEN_KEY) === live) return 'skipped';                    // reloaded for it once already
    storage.setItem(SEEN_KEY, live);
  } catch { /* storage blocked: reload anyway */ }
  reload();
  return 'reloaded';
}

/** Starts the watch; returns a stop function. */
export function watchBuild(deps: BuildWatchDeps & { now?: () => number }): () => void {
  const doc = deps.doc || document;
  const now = deps.now || Date.now;
  let last = now();
  let busy = false;
  const run = (force = false) => {
    if (busy || doc.visibilityState !== 'visible') return;
    if (!force && now() - last < BUILD_FOCUS_GAP_MS) return;
    busy = true; last = now();
    void checkBuild(deps).finally(() => { busy = false; });
  };
  const onVis = () => run();
  const onFocus = () => run();
  const id = window.setInterval(() => run(true), BUILD_CHECK_MS);
  doc.addEventListener('visibilitychange', onVis);
  window.addEventListener('focus', onFocus);
  return () => {
    window.clearInterval(id);
    doc.removeEventListener('visibilitychange', onVis);
    window.removeEventListener('focus', onFocus);
  };
}
