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
  } else if (ratingKey && kind === 'movie') {
    void json(`/api/plex/movie/${ratingKey}`);
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
let active = 0;

function pump(): void {
  while (active < WARM_CONCURRENCY && queue.length) {
    const job = queue.shift()!;
    started.add(job.key);
    active += 1;
    job.run().catch(() => {}).finally(() => { active -= 1; pump(); });
  }
}

/** Queue a warm-up; returns a cancel that drops it if it hasn't started. */
export function scheduleWarm(key: string, run: () => Promise<void>): () => void {
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
// Phone: open the popup only once it can show fully composed. The tapped
// poster gets a pressed state meanwhile; its own pixels seed the color blur so
// the sheet's first paint is the blur even at the cap.
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

export async function openWhenReady(
  kind: 'movie' | 'tv', ratingKey: number | undefined, tmdbId: number | undefined,
  card: HTMLElement | null, open: () => void,
): Promise<void> {
  if (pending) return;
  pending = true;
  tapAt = now();
  const seed = colorsFromImage(card?.querySelector('img') ?? null);
  if (seed) setSeedColors(kind, ratingKey, tmdbId, seed);
  card?.setAttribute('data-pressed', 'true');
  let timer = 0;
  try {
    await Promise.race([
      warmDetail(kind, ratingKey, tmdbId).catch(() => {}),
      new Promise<void>((res) => { timer = window.setTimeout(res, remainingCap(OPEN_CAP_MS)); }),
    ]);
  } finally {
    window.clearTimeout(timer);
    pending = false;
  }
  // Keep the pressed state until the sheet shows (or a short safety net).
  const done = () => { card?.removeAttribute('data-pressed'); window.removeEventListener(REVEALED_EVENT, done); };
  window.addEventListener(REVEALED_EVENT, done);
  window.setTimeout(done, 1500);
  open();
}

export function _resetModalPrefetch(): void {
  cache.clear();
  preloaded.clear();
  loading.clear();
  queue.length = 0;
  started.clear();
  active = 0;
  pending = false;
  tapAt = 0;
}
