/** First open like the second: the detail popup's metadata (Plex and TMDb
 * detail, the clearlogo lookup) is fetched through a short-lived shared cache,
 * and a card hover, focus or tap warms it, resolves the final backdrop and
 * logo URLs and starts loading those images, plus the color blur. When the
 * popup opens it reads the same cached responses, so images are already on
 * their way instead of waiting on a metadata round-trip. */
import { apiFetch } from '@/apiClient';
import { backdropUrl as tmdbBackdrop, logoImg } from '@/utils/image';
import { colorsFromImage } from '@/utils/posterColors';
import { finalBackdropUrl } from '@/utils/image';
import { parsePlexResponse, parsePlexTvResponse } from '@/utils/modalParsers';
import { getSeedColors, modalColors, prefetchModalColors, setSeedColors } from '@/services/modalColors';
import { isMinimal } from '@/minimal';

/** The sheet's ratings requests (the detail hooks read them through detailFetch, so a warm-up serves them). */
export function plexRatingsPath(ratingKey: number | string): string {
  return `/api/plex/ratings/${ratingKey}`;
}
export function titleRatingsPath(title: string, year?: number | string | null): string {
  const params = new URLSearchParams();
  if (year) params.set('year', String(year));
  return `/api/ratings/${encodeURIComponent(title)}?${params}`;
}
/** Background ratings warm-ups per Home load (every platform): the server counts them too, by WARM_HEADER, and
 *  also stops them near its daily OMDb budget. A user's open is never counted or capped. */
export const RATINGS_WARMS_PER_LOAD = 12;
export const WARM_HEADER = 'X-Luma-Warm';
let warmLoad = '';
let ratingsWarms = 0;
/** A new Home load: a fresh warm id and ratings count. */
export function startWarmLoad(): void {
  warmLoad = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  ratingsWarms = 0;
}
function takeRatingsWarm(): Record<string, string> | null {
  if (ratingsWarms >= RATINGS_WARMS_PER_LOAD) return null;
  if (!warmLoad) startWarmLoad();
  ratingsWarms += 1;
  return { [WARM_HEADER]: warmLoad };
}
function yearOf(d: unknown): number | null {
  const y = typeof d === 'string' ? Number(d.slice(0, 4)) : NaN;
  return Number.isFinite(y) && y > 0 ? y : null;
}

export const DETAIL_TTL_MS = 5 * 60 * 1000;
const MAX_ENTRIES = 150;

interface Cached { ok: boolean; status: number; body: string; type: string; warm?: boolean }
const cache = new Map<string, { at: number; p: Promise<Cached> }>();
const preloaded = new Map<string, HTMLImageElement>();

function load(url: string, warmHeaders?: Record<string, string>): Promise<Cached> {
  const hit = cache.get(url);
  if (hit && Date.now() - hit.at < DETAIL_TTL_MS) return hit.p;
  const p = apiFetch(url, warmHeaders ? { headers: warmHeaders } : undefined).then(async (r) => ({
    ok: r.ok, status: r.status, body: await r.text(), type: r.headers?.get?.('Content-Type') || 'application/json',
    warm: !!warmHeaders,
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
  // A warm-up the server skipped (quota guard) is no answer for an open: ask again as the open.
  const res = load(url).then((c) => (c.warm && !c.ok ? (cache.delete(url), load(url)) : c)).then((c) => new Response(c.body, { status: c.status || 500, headers: { 'Content-Type': c.type } }));
  if (!signal) return res;
  if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(new DOMException('Aborted', 'AbortError'));
    signal.addEventListener('abort', onAbort, { once: true });
    res.then((r) => { signal.removeEventListener('abort', onAbort); resolve(r); },
      (e) => { signal.removeEventListener('abort', onAbort); reject(e); });
  });
}

async function json(url: string, warmHeaders?: Record<string, string>): Promise<Record<string, unknown> | null> {
  try {
    const c = await load(url, warmHeaders);
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
/** `ratings`: also warm the title ratings (background warm-ups: capped per load, flagged for the server's
 *  budget guard); `open`: a user's open, which fetches them unflagged and uncapped. */
export async function warmDetail(
  kind: 'movie' | 'tv', ratingKey?: number, tmdbId?: number, opts: { images?: boolean; ratings?: boolean; open?: boolean } = {},
): Promise<void> {
  if (!ratingKey && !tmdbId) return;
  const images = opts.images !== false;
  prefetchModalColors(kind, ratingKey, tmdbId);
  let tid = tmdbId;
  let plexBackdrop: string | null = null;
  // Owned titles: the sheet asks for ratings by the Plex title and year, so the warm-up does too.
  let plexTitle: { title: string; year: number | null } | null = null;
  const plexPath = ratingKey ? (kind === 'movie' ? `/api/plex/movie/${ratingKey}` : `/api/plex/tv/${ratingKey}`) : null;
  const readPlex = (plex: Record<string, unknown> | null) => {
    if (!plex) return null;
    const parsed = kind === 'movie' ? parsePlexResponse(plex) : parsePlexTvResponse(plex);
    if (parsed.title) plexTitle = { title: parsed.title, year: parsed.year ?? null };
    return parsed;
  };
  let plexDone: Promise<unknown> = Promise.resolve();
  if (plexPath && !tmdbId) {
    const plex = await json(plexPath);
    const parsed = readPlex(plex);
    if (parsed && plex) {
      tid = (parsed.tmdbId as number | undefined) || (plex.tmdb_id as number | undefined) || undefined;
      plexBackdrop = parsed.backdropUrl || parsed.posterUrl || null;
    }
  } else if (plexPath) {
    // Both ids (owned search results): warm Plex too, which runs the
    // server-side tmdb_id type check the popup waits on.
    plexDone = json(plexPath).then(readPlex);
  }
  if (kind === 'movie' && ratingKey) void json(plexRatingsPath(ratingKey));
  const [tm, logo] = await Promise.all([
    tid ? json(kind === 'movie' ? `/api/movie/${tid}` : `/api/tv/${tid}`) : Promise.resolve(null),
    tid ? json(clearlogoPath(kind, tid)) : Promise.resolve(null),
  ]);
  // Ratings with the details: the title-based ratings the sheet asks for (none in the minimal build).
  if ((opts.ratings || opts.open) && !isMinimal()) {
    void plexDone.then(() => {
      const named = plexTitle as { title: string; year: number | null } | null;
      const title = named?.title ?? ((tm?.title ?? tm?.name) as string | undefined);
      if (!title) return;
      const year = named ? named.year : yearOf(tm?.release_date ?? tm?.first_air_date);
      const url = titleRatingsPath(title, year);
      if (opts.open) { void json(url); return; }
      if (cache.has(url)) return;
      const hdr = takeRatingsWarm();
      if (hdr) void json(url, hdr);
    });
  }
  if (!images) return; // metadata only (rows further down the list)
  const path = tm?.backdrop_path as string | undefined;
  await Promise.all([
    // The sheet's one backdrop (finalBackdropUrl): decoding it from the tap overlaps the sheet's enter motion.
    preloadImage(finalBackdropUrl(path ? tmdbBackdrop(path) : plexBackdrop)),
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

type Connection = { saveData?: boolean; effectiveType?: string; type?: string; metered?: boolean };
function connection(): Connection | null | undefined {
  if (typeof navigator === 'undefined') return undefined;
  return (navigator as Navigator & { connection?: Connection }).connection ?? null;
}

/** Background prefetch is off when the user asked to save data, the link is 2G, or it is metered or cellular
 *  (a tap still warms its own card). */
export function canPrefetch(): boolean {
  const c = connection();
  if (!c) return true;
  if (c.saveData || c.metered) return false;
  if (c.type === 'cellular') return false;
  return c.effectiveType !== '2g' && c.effectiveType !== 'slow-2g';
}

/** Without a Network Information API (iOS) the link can't be checked, so each surface warms at most this many
 *  titles in the background: the first screen's worth (two batches of the queue). */
export const UNKNOWN_LINK_CAP = WARM_CONCURRENCY * 2;
const groupCount = new Map<string, number>();
function groupAllows(group: string | undefined): boolean {
  if (!group || connection() !== null) return true;
  const n = groupCount.get(group) ?? 0;
  if (n >= UNKNOWN_LINK_CAP) return false;
  groupCount.set(group, n + 1);
  return true;
}
/** A new surface batch (a new search query): its background cap starts over. */
export function resetWarmGroup(group: string): void {
  groupCount.delete(group);
}

/** Queue a warm-up; returns a cancel that drops it if it hasn't started. */
export function scheduleWarm(key: string, run: () => Promise<void>, group?: string): () => void {
  if (!canPrefetch()) return () => {};
  if (started.has(key) || queue.some((j) => j.key === key)) return () => {};
  if (!groupAllows(group)) return () => {};
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

/** ms since the tap that opened the popup (null when it wasn't opened through openWhenReady just now). */
export function tapElapsed(): number | null {
  if (!tapAt) return null;
  const e = now() - tapAt;
  return e > 5000 ? null : e;
}

/** Time left on the cap counted from the TAP (detail fetch included), or
 * `fallback` when the popup wasn't opened through openWhenReady just now. */
export function remainingCap(fallback: number): number {
  if (!tapAt) return fallback;
  const elapsed = now() - tapAt;
  if (elapsed > 5000) return fallback;
  return Math.max(0, OPEN_CAP_MS - elapsed);
}

/** What the tapped card/row already shows (title, poster), for the sheet's seed shell (MOTION.md §1.2). */
/** `owned`: in the library (a Plex rating key, or the card's resolved ownership orb, data-owned); null when the
 *  card didn't know yet; `plex`: opened by a Plex rating key. The seed reserves the owned-only rows from them. */
export interface TapShell { title: string; year: string | null; poster: string | null; at: number; owned: boolean | null; plex: boolean }
function tapOwned(card: HTMLElement | null, ratingKey: number | undefined): boolean | null {
  if (ratingKey) return true;
  const o = card?.closest('[data-owned]')?.getAttribute('data-owned') ?? card?.querySelector('[data-owned]')?.getAttribute('data-owned');
  return o === 'true' ? true : o === 'false' ? false : null;
}
let shell: TapShell | null = null;
function tapTitle(card: HTMLElement): string {
  const t = card.getAttribute('aria-label') || card.querySelector('[data-testid="row-title"]')?.textContent
    || (card.innerText || card.textContent || '').split('\n')[0];
  return (t || '').trim();
}
/** The year the tapped card/row shows (a line that is just the year), for the seed's meta row. */
function tapYear(card: HTMLElement): string | null {
  const lines = (card.innerText || card.textContent || '').split('\n').map(l => l.trim());
  return lines.find(l => /^(18|19|20)\d{2}$/.test(l)) ?? null;
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
/** How long a tap waits for warm details before it opens the sheet anyway (on the seeded hero). With the
 *  mount and one primed frame this keeps tap-to-motion under OPEN_BUDGET_MS, also at 4x CPU (captap.py). */
export const OPEN_WAIT_MS = 0;   // one macrotask: details already warm (prefetched on hover/press) still win
/** How long a mounted sheet may still hold for its details: none after a tap (it already waited OPEN_WAIT_MS);
 *  OPEN_BUDGET_MS for a sheet opened any other way. */
export function budgetLeft(): number {
  if (tapAt && now() - tapAt < 5000) return 0;
  return OPEN_BUDGET_MS;
}
/** Fired by SheetFrame on the frame the phone sheet starts moving. */
export const SHEET_MOVING_EVENT = 'luma:sheet-moving';
/** Press feedback (Designer 10:16 PM): a .luma-pressable (Home card, search row) scales to 0.98 on pointerdown,
 *  on that frame, and stays pressed until its sheet starts moving; released on cancel, or soon after a release
 *  that opens nothing. Reduced motion: opacity only (index.css). */
let pressed: HTMLElement | null = null;
let pressTimer = 0;
function release(el: HTMLElement | null = pressed) {
  window.clearTimeout(pressTimer);
  el?.removeAttribute('data-pressed');
  if (el === pressed) { pressed = null; if (el) window.dispatchEvent(new Event(PRESS_RELEASED_EVENT)); }
}
/** A tap is pending: a card is pressed and its sheet hasn't started moving (or the press hasn't timed out).
 *  Work that can wait (Home's skeleton crossfade) holds until PRESS_RELEASED_EVENT so it never competes with
 *  the sheet's first frames. */
export const PRESS_RELEASED_EVENT = 'luma:press-released';
export function tapPending(): boolean { return pressed !== null; }
function holdFor(el: HTMLElement, ms: number) { window.clearTimeout(pressTimer); pressTimer = window.setTimeout(() => release(el), ms); }
if (typeof window !== 'undefined' && !(window as { __lumaPress?: boolean }).__lumaPress) {
  (window as { __lumaPress?: boolean }).__lumaPress = true;
  window.addEventListener('pointerdown', (e) => {
    const el = (e.target as Element | null)?.closest?.('.luma-pressable') as HTMLElement | null;
    if (!el) return;
    release(); pressed = el; el.setAttribute('data-pressed', 'true');
    holdFor(el, 1200);
  }, { capture: true, passive: true });
  window.addEventListener('pointerup', () => { if (pressed && !pending) holdFor(pressed, 250); }, { capture: true, passive: true });
  window.addEventListener('pointercancel', () => release(), { capture: true, passive: true });
  window.addEventListener(SHEET_MOVING_EVENT, () => release());
}

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
  shell = card ? { title: tapTitle(card), year: tapYear(card), poster: img?.currentSrc || img?.src || null, at: now(), owned: tapOwned(card, ratingKey), plex: !!ratingKey } : null;
  if (card) { if (pressed && pressed !== card) release(); pressed = card; card.setAttribute('data-pressed', 'true'); holdFor(card, SHEET_MOUNT_WAIT_MS); }
  let timer = 0;
  try {
    await Promise.race([
      warmDetail(kind, ratingKey, tmdbId, { open: true }).catch(() => {}),
      new Promise<void>((res) => { timer = window.setTimeout(res, OPEN_WAIT_MS); }),
    ]);
  } finally {
    window.clearTimeout(timer);
  }
  try {
    open();
  } catch (e) {
    pending = false;
    release(card);
    throw e;
  }
  // The press is released by the sheet's first moving frame (SHEET_MOVING_EVENT); holdFor is the fallback.
  watchSheet(() => { pending = false; onClosed?.(); });
}

export function _resetModalPrefetch(): void {
  cache.clear();
  preloaded.clear();
  loading.clear();
  queue.length = 0;
  started.clear();
  groupCount.clear();
  warmLoad = '';
  ratingsWarms = 0;
  gen += 1;
  active = 0;
  pending = false;
  tapAt = 0;
  shell = null;
}
