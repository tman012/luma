/** First open like the second: the detail popup's metadata (Plex and TMDb
 * detail, the clearlogo lookup) is fetched through a short-lived shared cache,
 * and a card hover, focus or tap warms it, resolves the final backdrop and
 * logo URLs and starts loading those images, plus the color blur. When the
 * popup opens it reads the same cached responses, so images are already on
 * their way instead of waiting on a metadata round-trip. */
import { apiFetch } from '@/apiClient';
import { backdropUrl as tmdbBackdrop, logoImg } from '@/utils/image';
import { colorsFromImage } from '@/utils/posterColors';
import { parsePlexResponse, parsePlexTvResponse } from '@/utils/modalParsers';
import { getSeedColors, modalColors, prefetchModalColors, setSeedColors } from '@/services/modalColors';

export const DETAIL_TTL_MS = 5 * 60 * 1000;
const MAX_ENTRIES = 150;

interface Cached { ok: boolean; status: number; body: string; type: string }
const cache = new Map<string, { at: number; p: Promise<Cached> }>();
const preloaded = new Map<string, HTMLImageElement>();

function load(url: string): Promise<Cached> {
  const hit = cache.get(url);
  if (hit && Date.now() - hit.at < DETAIL_TTL_MS) return hit.p;
  const p = apiFetch(url).then(async (r) => ({
    ok: r.ok, status: r.status, body: await r.text(), type: r.headers?.get?.('Content-Type') || 'application/json',
  }));
  cache.set(url, { at: Date.now(), p });
  if (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value as string);
  // Errors aren't kept: the next open retries.
  p.then((c) => { if (!c.ok) cache.delete(url); }, () => cache.delete(url));
  return p;
}

/** apiFetch for the popup's read-only metadata, served from the shared cache. */
export function detailFetch(url: string, init?: { signal?: AbortSignal }): Promise<Response> {
  const signal = init?.signal;
  const res = load(url).then((c) => new Response(c.body, { status: c.status || 500, headers: { 'Content-Type': c.type } }));
  if (!signal) return res;
  if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(new DOMException('Aborted', 'AbortError'));
    signal.addEventListener('abort', onAbort, { once: true });
    res.then((r) => { signal.removeEventListener('abort', onAbort); resolve(r); },
      (e) => { signal.removeEventListener('abort', onAbort); reject(e); });
  });
}

async function json(url: string): Promise<Record<string, unknown> | null> {
  try {
    const c = await load(url);
    return c.ok ? (JSON.parse(c.body) as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** Load and decode an image (kept referenced); resolves when ready or failed. */
const loading = new Map<string, Promise<void>>();
export function preloadImage(url: string | null | undefined): Promise<void> {
  if (!url || typeof Image === 'undefined') return Promise.resolve();
  const hit = loading.get(url);
  if (hit) return hit;
  const img = new Image();
  img.decoding = 'async';
  const p = new Promise<void>((resolve) => {
    img.onload = () => {
      const d = typeof img.decode === 'function' ? img.decode() : Promise.resolve();
      d.then(() => resolve(), () => resolve());
    };
    img.onerror = () => { loading.delete(url); resolve(); };
  });
  img.src = url;
  preloaded.set(url, img);
  loading.set(url, p);
  if (preloaded.size > MAX_ENTRIES) {
    const k = preloaded.keys().next().value as string;
    preloaded.delete(k); loading.delete(k);
  }
  return p;
}

export function clearlogoPath(kind: 'movie' | 'tv', tmdbId: number): string {
  return `/api/clearlogo/${tmdbId}${kind === 'tv' ? '?type=tv' : ''}`;
}

/** Warm everything a movie/TV popup opened with these ids will need. */
export async function warmDetail(
  kind: 'movie' | 'tv', ratingKey?: number, tmdbId?: number, opts: { images?: boolean } = {},
): Promise<void> {
  if (!ratingKey && !tmdbId) return;
  const images = opts.images !== false;
  prefetchModalColors(kind, ratingKey, tmdbId);
  let tid = tmdbId;
  let plexBackdrop: string | null = null;
  if (ratingKey && !tmdbId) {
    const plex = await json(kind === 'movie' ? `/api/plex/movie/${ratingKey}` : `/api/plex/tv/${ratingKey}`);
    if (plex) {
      const parsed = kind === 'movie' ? parsePlexResponse(plex) : parsePlexTvResponse(plex);
      tid = (parsed.tmdbId as number | undefined) || (plex.tmdb_id as number | undefined) || undefined;
      plexBackdrop = parsed.backdropUrl || parsed.posterUrl || null;
    }
  } else if (ratingKey) {
    // Both ids (owned search results): warm Plex too, which runs the
    // server-side tmdb_id type check the popup waits on.
    void json(kind === 'movie' ? `/api/plex/movie/${ratingKey}` : `/api/plex/tv/${ratingKey}`);
  }
  const [tm, logo] = await Promise.all([
    tid ? json(kind === 'movie' ? `/api/movie/${tid}` : `/api/tv/${tid}`) : Promise.resolve(null),
    tid ? json(clearlogoPath(kind, tid)) : Promise.resolve(null),
  ]);
  if (!images) return; // metadata only (rows further down the list)
  const path = tm?.backdrop_path as string | undefined;
  await Promise.all([
    preloadImage(path ? tmdbBackdrop(path) : plexBackdrop),
    preloadImage(logoImg((logo?.clearlogo_url as string | undefined) ?? null)),
    // With a poster-derived seed the palette never gates the reveal.
    getSeedColors(kind, ratingKey, tmdbId) ? Promise.resolve(null) : modalColors(kind, ratingKey, tmdbId),
  ]);
}

// ---------------------------------------------------------------------------
// Phone: warm cards that are on (or near) the screen, at most WARM_CONCURRENCY
// at a time; a card that scrolls away before its turn is dropped.
// ---------------------------------------------------------------------------

export const WARM_CONCURRENCY = 4;
const queue: { key: string; run: () => Promise<void> }[] = [];
const started = new Set<string>();
/** Warm-ups remembered so a title isn't warmed twice; the oldest are
 *  forgotten past this (Set keeps insertion order). */
export const STARTED_MAX = 500;
let active = 0;
/** Bumped by a reset: jobs from before it finish without touching `active`. */
let gen = 0;

function pump(): void {
  while (active < WARM_CONCURRENCY && queue.length) {
    const job = queue.shift()!;
    started.add(job.key);
    if (started.size > STARTED_MAX) started.delete(started.values().next().value as string);
    active += 1;
    const g = gen;
    job.run().catch(() => {}).finally(() => {
      if (g !== gen) return; // reset meanwhile: its slot was already freed
      active -= 1;
      pump();
    });
  }
}

/** Background prefetch is off when the user asked to save data or the link
 *  is 2G (a tap still warms its own card). */
export function canPrefetch(): boolean {
  if (typeof navigator === 'undefined') return true;
  const c = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
  if (!c) return true;
  if (c.saveData) return false;
  return c.effectiveType !== '2g' && c.effectiveType !== 'slow-2g';
}

/** Queue a warm-up; returns a cancel that drops it if it hasn't started. */
export function scheduleWarm(key: string, run: () => Promise<void>): () => void {
  if (!canPrefetch()) return () => {};
  if (started.has(key) || queue.some((j) => j.key === key)) return () => {};
  const job = { key, run };
  queue.push(job);
  pump();
  return () => {
    const i = queue.indexOf(job);
    if (i >= 0) queue.splice(i, 1);
  };
}

// ---------------------------------------------------------------------------
// Phone: a tap opens the sheet within OPEN_BUDGET_MS (150 ms, MOTION.md §1.1),
// sooner when the title is warm; the sheet slides in opaque with the seed shell
// and fills in place. The tapped poster's pixels seed the color blur. OPEN_CAP_MS
// is only how long the detail hooks still wait for the logo/colors (remainingCap).
// ---------------------------------------------------------------------------

export const OPEN_CAP_MS = 600;
export const REVEALED_EVENT = 'luma:detail-revealed';
let pending = false;
let tapAt = 0;
const now = () => Date.now();

/** Time left on the cap counted from the TAP (detail fetch included), or
 * `fallback` when the popup wasn't opened through openWhenReady just now. */
export function remainingCap(fallback: number): number {
  if (!tapAt) return fallback;
  const elapsed = now() - tapAt;
  if (elapsed > 5000) return fallback;
  return Math.max(0, OPEN_CAP_MS - elapsed);
}

/** What the tapped card/row already shows (title, poster), for the sheet's seed shell (MOTION.md §1.2). */
export interface TapShell { title: string; poster: string | null; at: number }
let shell: TapShell | null = null;
function tapTitle(card: HTMLElement): string {
  const t = card.getAttribute('aria-label') || card.querySelector('[data-testid="row-title"]')?.textContent
    || (card.innerText || card.textContent || '').split('\n')[0];
  return (t || '').trim();
}
/** The seed shell of the tap that is opening a sheet now (none after 5 s). */
export function tapShell(): TapShell | null {
  return shell && now() - shell.at < 5000 ? shell : null;
}

const SHEET_SEL = '[data-modal="MovieDetailModal"],[data-modal="TvDetailModal"]';
/** How long a tap waits for a detail sheet to mount before giving up on it (no sheet: release). */
export const SHEET_MOUNT_WAIT_MS = 1500;

/** Calls `onClosed` once the detail sheet that is about to mount (or already mounted) is gone again;
 *  if none mounts within SHEET_MOUNT_WAIT_MS, calls it then. */
export function watchSheet(onClosed: () => void): void {
  let seen = !!document.querySelector(SHEET_SEL);
  let done = false;
  const finish = () => { if (done) return; done = true; obs.disconnect(); window.clearTimeout(t); onClosed(); };
  const obs = new MutationObserver(() => {
    const now = !!document.querySelector(SHEET_SEL);
    if (now) seen = true;
    else if (seen) finish();
  });
  obs.observe(document.body, { childList: true, subtree: true });
  const t = window.setTimeout(() => { if (!seen) finish(); }, SHEET_MOUNT_WAIT_MS);
}

/** MOTION.md §1.3: the tap waits at most this long for warm details, then opens with the seed shell. */
export const OPEN_BUDGET_MS = 150;

/** Home card and search result taps (one path): pressed state on the tapped element, warm details for up
 *  to OPEN_BUDGET_MS, then `open()`; the press releases as the sheet starts entering (next frame).
 *  While that sheet is open further taps are ignored (a double tap or a 2nd Enter never opens or
 *  pushes it twice); `onClosed` runs when it closes (search UIs stay mounted under it and use this to
 *  come back). */
export async function openWhenReady(
  kind: 'movie' | 'tv', ratingKey: number | undefined, tmdbId: number | undefined,
  card: HTMLElement | null, open: () => void, onClosed?: () => void,
): Promise<void> {
  if (pending) return;
  pending = true;
  tapAt = now();
  const img = card?.querySelector('img') ?? null;
  const seed = colorsFromImage(img);
  if (seed) setSeedColors(kind, ratingKey, tmdbId, seed);
  shell = card ? { title: tapTitle(card), poster: img?.currentSrc || img?.src || null, at: now() } : null;
  card?.setAttribute('data-pressed', 'true');
  let timer = 0;
  try {
    await Promise.race([
      warmDetail(kind, ratingKey, tmdbId).catch(() => {}),
      new Promise<void>((res) => { timer = window.setTimeout(res, OPEN_BUDGET_MS); }),
    ]);
  } finally {
    window.clearTimeout(timer);
  }
  try {
    open();
  } catch (e) {
    pending = false;
    card?.removeAttribute('data-pressed');
    throw e;
  }
  requestAnimationFrame(() => card?.removeAttribute('data-pressed'));
  watchSheet(() => { pending = false; onClosed?.(); });
}

export function _resetModalPrefetch(): void {
  cache.clear();
  preloaded.clear();
  loading.clear();
  queue.length = 0;
  started.clear();
  gen += 1;
  active = 0;
  pending = false;
  tapAt = 0;
  shell = null;
}
