import { _resetImageLoader, loadImage } from '@/loading/imageLoader';
/** First open like the second: the detail popup's metadata (Plex and TMDb
 * detail, the clearlogo lookup) is fetched through a short-lived shared cache,
 * and a card hover, focus or tap warms it, resolves the final backdrop and
 * logo URLs and starts loading those images, plus the color blur. When the
 * popup opens it reads the same cached responses, so images are already on
 * their way instead of waiting on a metadata round-trip. */
import { _resetSheetPriority, afterSheetPriority, beginSheetPriority, endSheetPriority, keepHomePostersOnOpen, SEARCH_SURFACE, sheetOpening, sheetOpens } from '@/loading/sheetPriority';
import { SHEET_OPEN_BOUND_MS } from '@/loading/sheetOpenGate';
import { apiFetch } from '@/apiClient';
import { backdropUrl as tmdbBackdrop, logoImg } from '@/utils/image';
import { colorsFromImage } from '@/utils/posterColors';
import { yieldToMain } from '@/utils/scheduling';
import { finalBackdropUrl, plexSheetArt } from '@/utils/image';
import { localBackdropFor, localLogoFor } from '@/services/localArt';
import { parsePlexResponse, parsePlexTvResponse } from '@/utils/modalParsers';
import { dropOtherColors, getSeedColors, modalColors, prefetchModalColors, setSeedColors } from '@/services/modalColors';
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
/** Search's ratings warm-ups per query (they count against the load's cap and the server budget too). */
export const SEARCH_RATINGS_WARMS = 4;
let searchRatingsWarms = 0;
/** A new search result set: its ratings warm count starts over. */
export function startSearchWarms(): void {
  searchRatingsWarms = 0;
  searchQuery += 1;
}
/** The warm id background requests carry (the server caps OMDb per id: per Home load, and per query under it). */
let searchQuery = 0;
function warmHeaders(search: boolean): Record<string, string> {
  if (!warmLoad) startWarmLoad();
  return { [WARM_HEADER]: search ? `${warmLoad}/${searchQuery}` : warmLoad };
}
function takeRatingsWarm(search: boolean): Record<string, string> | null {
  if (ratingsWarms >= RATINGS_WARMS_PER_LOAD) return null;
  if (search) {
    if (searchRatingsWarms >= SEARCH_RATINGS_WARMS) return null;
    searchRatingsWarms += 1;
  }
  ratingsWarms += 1;
  return warmHeaders(search);
}
function yearOf(d: unknown): number | null {
  const y = typeof d === 'string' ? Number(d.slice(0, 4)) : NaN;
  return Number.isFinite(y) && y > 0 ? y : null;
}

export const DETAIL_TTL_MS = 5 * 60 * 1000;
const MAX_ENTRIES = 150;

interface Cached { ok: boolean; status: number; body: string; type: string; warm?: boolean }
const cache = new Map<string, { at: number; p: Promise<Cached>; ac?: AbortController | null }>();

/** Detail requests still on the wire, so an open can drop other titles' warm-ups (an HTTP/1.1 origin has 6
 *  connections; slow TMDb warm-ups held them all and the sheet's backdrop waited behind them). */
const inflight = new Map<string, AbortController>();
/** The warm-up chains (their title ids) each in-flight request serves: a chain warming the opened title keeps its
 *  requests (a Home card known only by its Plex key still owns its TMDb details request). */
type TitleIds = Set<number>;
const owners = new Map<string, TitleIds[]>();
/** Aborts in-flight detail requests that aren't for this title (they're forgotten, so a later open refetches). */
function dropOtherWarms(ids: (number | undefined)[], title?: string): void {
  dropOtherColors(ids.filter((x): x is number => !!x));
  const keep = ids.filter((x): x is number => !!x).map((x) => new RegExp(`/${x}(?:[/?&]|$)`));
  // The opened title's own ratings warm is keyed by title, not id: it stays.
  const ratingsOfTitle = title ? titleRatingsPath(title).split('?')[0] + '?' : null;
  for (const [url, ac] of Array.from(inflight)) {
    if (keep.some((re) => re.test(url)) || (ratingsOfTitle && url.startsWith(ratingsOfTitle))) continue;
    if ((owners.get(url) ?? []).some((o) => ids.some((x) => !!x && o.has(x)))) continue;
    inflight.delete(url); ac.abort();
    // Only this request's own entry is forgotten (a newer one for the same URL stays).
    const e = cache.get(url); if (e && e.ac === ac) cache.delete(url);
  }
}

function load(url: string, warmHeaders?: Record<string, string>): Promise<Cached> {
  const hit = cache.get(url);
  if (hit && Date.now() - hit.at < DETAIL_TTL_MS) return hit.p;
  const ac = typeof AbortController === 'undefined' ? null : new AbortController();
  const init: RequestInit | undefined = warmHeaders || ac ? { ...(warmHeaders ? { headers: warmHeaders } : {}), ...(ac ? { signal: ac.signal } : {}) } : undefined;
  const p = apiFetch(url, init).then(async (r) => ({
    ok: r.ok, status: r.status, body: await r.text(), type: r.headers?.get?.('Content-Type') || 'application/json',
    warm: !!warmHeaders,
  }));
  const entry = { at: Date.now(), p, ac };
  cache.set(url, entry);
  if (ac) { inflight.set(url, ac); owners.delete(url); const done = () => { if (inflight.get(url) === ac) { inflight.delete(url); owners.delete(url); } }; p.then(done, done); }
  if (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value as string);
  // Errors aren't kept: the next open retries.
  // Only if the stored entry is still this one (an aborted request never deletes a newer entry for the URL).
  const forget = () => { if (cache.get(url) === entry) cache.delete(url); };
  p.then((c) => { if (!c.ok) forget(); }, forget);
  return p;
}

/** Drops every cached detail/ratings answer (in memory), e.g. after Fix Match moved an item to another title. */
export function invalidateDetails(): void {
  cache.clear();
}

/** apiFetch for the popup's read-only metadata, served from the shared cache. */
export function detailFetch(url: string, init?: { signal?: AbortSignal }): Promise<Response> {
  const signal = init?.signal;
  // A warm-up the server skipped (quota guard) is no answer for an open: ask again as the open.
  const res = load(url).then((c) => (c.warm && (!c.ok || c.body.includes('"ratings_skipped":true')) ? (cache.delete(url), load(url)) : c)).then((c) => new Response(c.body, { status: c.status || 500, headers: { 'Content-Type': c.type } }));
  if (!signal) return res;
  if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(new DOMException('Aborted', 'AbortError'));
    signal.addEventListener('abort', onAbort, { once: true });
    res.then((r) => { signal.removeEventListener('abort', onAbort); resolve(r); },
      (e) => { signal.removeEventListener('abort', onAbort); reject(e); });
  });
}

async function json(url: string, warmHeaders?: Record<string, string>, owner?: TitleIds): Promise<Record<string, unknown> | null> {
  try {
    const pending = load(url, warmHeaders);
    if (owner && inflight.has(url)) owners.set(url, [...(owners.get(url) ?? []), owner]);
    const c = await pending;
    return c.ok ? (JSON.parse(c.body) as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** Load and decode an image through the app's memory-only image loader; resolves when ready or failed. */
export function preloadImage(url: string | null | undefined, priority?: 'high' | 'low'): Promise<void> {
  return loadImage(url, priority);
}

export function clearlogoPath(kind: 'movie' | 'tv', tmdbId: number): string {
  return `/api/clearlogo/${tmdbId}${kind === 'tv' ? '?type=tv' : ''}`;
}

/** cw-logo: a logo answer the server already gave on the card (url null: known to have none). */
export interface KnownLogo { kind: 'movie' | 'tv'; tmdbId: number; url: string | null }
/** Seeds the sheet's clearlogo lookup with the card's known answer (memory only, the shared detail cache's TTL), so
 *  the sheet settles its logo the moment it knows the id, without a round trip. A real answer already there wins. */
export function primeClearlogo(k: KnownLogo | null | undefined): void {
  if (!k || !k.tmdbId) return;
  const url = clearlogoPath(k.kind, k.tmdbId);
  const hit = cache.get(url);
  if (hit && Date.now() - hit.at < DETAIL_TTL_MS) return;
  const body = JSON.stringify({ clearlogo_url: k.url });
  cache.set(url, { at: Date.now(), p: Promise.resolve({ ok: true, status: 200, body, type: 'application/json' }), ac: null });
  if (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value as string);
}

/** A failed clearlogo lookup is retried this often, this far apart, before the title settles as text. */
export const LOGO_RETRIES = 2;
export const LOGO_RETRY_MS = 120;
/** The sheet's clearlogo lookup (every path: Home, Continue Watching, search): the logo image URL, or null for a real
 *  "no logo". A failed lookup (5xx, network) is not "no logo": it is asked again (a fresh request, the shared cache
 *  never keeps errors) before it settles. The sheet's one reveal still decides logo vs text by its deadline. */
export async function fetchClearlogo(kind: 'movie' | 'tv', tmdbId: number, signal?: AbortSignal): Promise<string | null> {
  // v344: no TMDb id, no lookup (never /api/clearlogo/None).
  if (!Number.isInteger(Number(tmdbId)) || Number(tmdbId) <= 0) return null;
  for (let attempt = 0; ; attempt += 1) {
    try {
      const resp = await detailFetch(clearlogoPath(kind, tmdbId), signal ? { signal } : undefined);
      if (resp.ok) {
        const url = logoImg(((await resp.json()) as { clearlogo_url?: string | null }).clearlogo_url ?? null);
        // v345: a sheet's logo starts loading the moment its URL is known (high priority, never queued behind the hold);
        // the hero and title reuse this load.
        if (url && !localLogoFor(kind, tmdbId)) void preloadImage(url, 'high');   // v350: a stored logo: never TMDb's too
        return url;
      }
    } catch (e) {
      if ((e as { name?: string })?.name === 'AbortError' || signal?.aborted) return null;
    }
    if (attempt >= LOGO_RETRIES || signal?.aborted) return null;
    await new Promise<void>((r) => window.setTimeout(r, LOGO_RETRY_MS));
  }
}

/** Warm everything a movie/TV popup opened with these ids will need. */
/** `ratings`: also warm the title ratings (background warm-ups: capped per load, flagged for the server's
 *  budget guard); `open`: a user's open, which fetches them unflagged and uncapped. */
const skippedWarms = new Map<string, () => void>();
const MAX_SKIPPED_WARMS = 12;
function queueWarm(kind: 'movie' | 'tv', ratingKey: number | undefined, tmdbId: number | undefined,
  opts: Parameters<typeof warmDetail>[3]): void {
  const k = `${kind}:${ratingKey ?? ''}:${tmdbId ?? ''}`;
  skippedWarms.delete(k);
  skippedWarms.set(k, () => { void warmDetail(kind, ratingKey, tmdbId, opts).catch(() => {}); });
  while (skippedWarms.size > MAX_SKIPPED_WARMS) skippedWarms.delete(skippedWarms.keys().next().value as string);
  afterSheetPriority(flushSkippedWarms); // one listener (a Set) however many are queued
}
function flushSkippedWarms(): void {
  const fns = Array.from(skippedWarms.values()); skippedWarms.clear();
  for (const f of fns) f();
}
export async function warmDetail(
  kind: 'movie' | 'tv', ratingKey?: number, tmdbId?: number,
  opts: { images?: boolean; ratings?: boolean; open?: boolean; search?: boolean; art?: (string | null | undefined)[]; logo?: KnownLogo } = {},
): Promise<void> {
  if (!ratingKey && !tmdbId) return;
  // cw-logo: a logo the card already names seeds the lookup (any warm-up; nothing is fetched for it here).
  primeClearlogo(opts.logo);
  // A background warm-up (hover, row focus) never starts while a sheet opens: the open's art and details own the
  // connections until its reveal (v341l: hover warms of other rows held the sockets the tapped art needed).
  // v341n: it is queued, not dropped: it runs once the window ends (one per title; the newest options win).
  if (!opts.open && sheetOpening()) { queueWarm(kind, ratingKey, tmdbId, opts); return; }
  // The card's or row's own art (the sheet's exact URLs): loading from the warm-up or the tap, not after the details.
  if (opts.images !== false) for (const u of opts.art ?? []) if (u) void preloadImage(u, opts.open ? 'high' : 'low');
  // Background detail requests carry the warm id: their inline ratings stay inside the server's OMDb guards.
  const bg = opts.open ? undefined : warmHeaders(!!opts.search);
  const images = opts.images !== false;
  prefetchModalColors(kind, ratingKey, tmdbId);
  let tid = tmdbId;
  // This chain's title ids (an open keeps the requests of the chain warming its title).
  const own = new Set<number>([ratingKey, tmdbId].filter((x): x is number => !!x));
  // A background warm-up stops at its next step while a sheet opens (an aborted step must not start the next).
  const yieldToSheet = () => !opts.open && sheetOpening();
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
  // A user's open: the Plex art (the sheet's art while TMDb is still out) starts at high priority as soon as the Plex
  // details name it, not after TMDb answers, so a slow TMDb never leaves the art deadline without a backdrop.
  const earlyArt = (u: string | null) => { if (opts.open && images && u) void preloadImage(finalBackdropUrl(u), 'high'); };
  let plexDone: Promise<unknown> = Promise.resolve();
  if (plexPath && !tmdbId) {
    const plex = await json(plexPath, bg, own);
    if (yieldToSheet()) return;
    const parsed = readPlex(plex);
    if (parsed && plex) {
      tid = (parsed.tmdbId as number | undefined) || (plex.tmdb_id as number | undefined) || undefined;
      plexBackdrop = parsed.backdropUrl || parsed.posterUrl || null;
    }
    if (tid) own.add(tid);
    // A movie with a TMDb id parses no backdrop (TMDb's is the pick): its Plex art is still the art to start now.
    earlyArt(parsed?.backdropUrl ?? plexSheetArt((parsed as { plexBackdropUrl?: string | null } | null)?.plexBackdropUrl));
  } else if (plexPath) {
    // Both ids (owned search results): warm Plex too, which runs the
    // server-side tmdb_id type check the popup waits on.
    plexDone = json(plexPath, bg, own).then(readPlex).then((p) => {
      earlyArt((p?.backdropUrl as string | undefined) ?? plexSheetArt((p as { plexBackdropUrl?: string | null } | null)?.plexBackdropUrl));
      return p;
    });
  }
  if (kind === 'movie' && ratingKey) void json(plexRatingsPath(ratingKey));
  const logoAsk = tid ? json(clearlogoPath(kind, tid), undefined, own) : Promise.resolve(null);
  // v345 (prod's order): the open's logo image starts the moment its URL is known, at high priority, never after the
  // details or behind the background pause; the sheet's hero and title reuse this load (reuseLoad).
  if (opts.open && images) void logoAsk.then((l) => preloadImage(localLogoFor(kind, tid) || logoImg((l?.clearlogo_url as string | undefined) ?? null), 'high'));
  const [tm, logo] = await Promise.all([
    tid ? json(kind === 'movie' ? `/api/movie/${tid}` : `/api/tv/${tid}`, bg, own) : Promise.resolve(null),
    logoAsk,
  ]);
  // Ratings with the details: the title-based ratings the sheet asks for (none in the minimal build).
  // Only when the detail reply carried no inline ratings (an older server) is the ratings endpoint warmed.
  if ((opts.ratings || opts.open) && !isMinimal() && tm?.ratings === undefined) {
    void plexDone.then(() => {
      const named = plexTitle as { title: string; year: number | null } | null;
      const title = named?.title ?? ((tm?.title ?? tm?.name) as string | undefined);
      if (!title) return;
      const year = named ? named.year : yearOf(tm?.release_date ?? tm?.first_air_date);
      const url = titleRatingsPath(title, year);
      if (opts.open) { void json(url); return; }
      if (cache.has(url)) return;
      const hdr = takeRatingsWarm(!!opts.search);
      if (hdr) void json(url, hdr);
    });
  }
  if (yieldToSheet()) return;
  if (!images) return; // metadata only (rows further down the list)
  const path = tm?.backdrop_path as string | undefined;
  await Promise.all([
    // The sheet's one backdrop (finalBackdropUrl): decoding it from the tap overlaps the sheet's enter motion.
    // An owned title whose Plex art the card/row named shows that art only: never TMDb's backdrop too (v341k).
    // wl-images v350: stored art in place of TMDb's (never both); after a drop (error / wait) TMDb's again.
    preloadImage((ratingKey && (opts.art ?? []).find(Boolean)) || localBackdropFor(kind, tid) || finalBackdropUrl(path ? tmdbBackdrop(path) : plexBackdrop), opts.open ? 'high' : 'low'),
    preloadImage(localLogoFor(kind, tid) || logoImg((logo?.clearlogo_url as string | undefined) ?? null), opts.open ? 'high' : 'low'),
    // With a poster-derived seed the palette never gates the reveal.
    getSeedColors(kind, ratingKey, tmdbId) ? Promise.resolve(null) : modalColors(kind, ratingKey, tmdbId),
  ]);
}

// ---------------------------------------------------------------------------
// Phone: warm cards that are on (or near) the screen, at most WARM_CONCURRENCY
// at a time; a card that scrolls away before its turn is dropped.
// ---------------------------------------------------------------------------

export const WARM_CONCURRENCY = 4;
const queue: { key: string; run: () => Promise<void>; retries?: number }[] = [];
/** A warm-up an open cut short (dropOtherWarms aborts its requests, yieldToSheet stops it) runs again after the
 *  reveal, at most this often; its finished requests are cache hits. */
export const WARM_RETRIES = 2;
const started = new Set<string>();
/** Warm-ups remembered so a title isn't warmed twice; the oldest are
 *  forgotten past this (Set keeps insertion order). */
export const STARTED_MAX = 500;
let active = 0;
/** Bumped by a reset: jobs from before it finish without touching `active`. */
let gen = 0;

function pump(): void {
  // A sheet opening: warm-ups wait for its reveal (its art owns the connections).
  if (sheetOpening()) { afterSheetPriority(pump); return; }
  while (active < WARM_CONCURRENCY && queue.length) {
    const job = queue.shift()!;
    started.add(job.key);
    if (started.size > STARTED_MAX) started.delete(started.values().next().value as string);
    active += 1;
    const g = gen;
    const o = sheetOpens();
    job.run().catch(() => {}).finally(() => {
      if (g !== gen) return; // reset meanwhile: its slot was already freed
      active -= 1;
      // Ran across an open: queued again so the title stays warm (a warm open, not a cold one, on its tap).
      if (sheetOpens() !== o && (job.retries ?? 0) < WARM_RETRIES) { job.retries = (job.retries ?? 0) + 1; started.delete(job.key); queue.push(job); }
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
// A tap opens the detail sheet in the same task, into its final frame; the title, logo and artwork reveal into it
// once they are ready (useOneReveal), never held for the network. The tapped poster's pixels seed the colors and
// the title for that first frame.
// ---------------------------------------------------------------------------

export const OPEN_CAP_MS = 600;
export const REVEALED_EVENT = 'luma:detail-revealed';
let pending = false;
let tapAt = 0;
/** Bumped by every open attempt. A newer tap supersedes one whose sheet is not on screen yet. */
let openGen = 0;
let stopWatch: (() => void) | null = null;
/** The title this module just asked the host to open, until its sheet is on screen: the same title tapped again in
 *  that window is the same open (a double tap), not a second one. */
let opened: { key: string; at: number } | null = null;
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
/** The first non-empty text node of a card (no layout flush, unlike innerText). */
function firstTextLine(card: HTMLElement): string {
  const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = (n.nodeValue ?? '').trim();
    if (t) return t;
  }
  return '';
}
function tapTitle(card: HTMLElement): string {
  const t = card.getAttribute('aria-label') || card.querySelector('[data-testid="row-title"]')?.textContent || firstTextLine(card);
  return (t || '').trim();
}
/** The year the tapped card/row shows (a line that is just the year), for the seed's meta row. */
function tapYear(card: HTMLElement): string | null {
  // Text nodes, not innerText: innerText flushes style and layout inside the tap's task.
  const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = (n.nodeValue ?? '').trim();
    if (/^(18|19|20)\d{2}$/.test(t)) return t;
  }
  return null;
}
/** The seed shell of the tap that is opening a sheet now (none after 5 s). */
export function tapShell(): TapShell | null {
  return shell && now() - shell.at < 5000 ? shell : null;
}

const SHEET_SEL = '[data-modal="MovieDetailModal"],[data-modal="TvDetailModal"]';
/** A sheet that is on screen and not on its way out. One that is exiting no longer swallows a tap. */
const LIVE_SHEET_SEL = '[data-modal="MovieDetailModal"]:not([data-phase="exiting"]),[data-modal="TvDetailModal"]:not([data-phase="exiting"])';
/** How long a tap waits for a detail sheet to mount before giving up on it (no sheet: release). */
export const SHEET_MOUNT_WAIT_MS = 1500;

/** Calls `onClosed` once the detail sheet that is about to mount (or already mounted) is gone again.
 *  If none mounts within the open bound, calls it then. Returns a cancel that does not call `onClosed`. */
export function watchSheet(onClosed: () => void): () => void {
  let seen = !!document.querySelector(SHEET_SEL);
  let done = false;
  const finish = () => { if (done) return; done = true; obs.disconnect(); window.clearTimeout(t); onClosed(); };
  const obs = new MutationObserver(() => {
    const nowSeen = !!document.querySelector(SHEET_SEL);
    if (nowSeen) seen = true;
    else if (seen) finish();
  });
  obs.observe(document.body, { childList: true, subtree: true });
  const t = window.setTimeout(() => { if (!seen) finish(); }, SHEET_OPEN_BOUND_MS);
  return () => { if (done) return; done = true; obs.disconnect(); window.clearTimeout(t); };
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
  card: HTMLElement | null, open: () => void, onClosed?: () => void, art?: (string | null | undefined)[],
  logo?: KnownLogo,
): Promise<void> {
  // A sheet already on screen ignores further taps. One that is closing does not: its exit plays on while this
  // tap opens the next title (A→B→C must not let an older title reveal: each open supersedes the last).
  const openKey = `${kind}:${ratingKey ?? ''}:${tmdbId ?? ''}`;
  if (pending && (!!document.querySelector(LIVE_SHEET_SEL) || (opened?.key === openKey && now() - opened.at < SHEET_MOUNT_WAIT_MS && !document.querySelector(SHEET_SEL)))) return;
  const mine = ++openGen;
  stopWatch?.();
  stopWatch = null;
  pending = true;
  // cw-logo: a logo the card names starts downloading at the tap, at high priority (the open's hold pauses and drops
  // only low-priority warm-ups; this load is the sheet's own: the hero's decode reuses it, see useLoadedImage).
  primeClearlogo(logo);
  const logoUrl = logo?.url && !localLogoFor(kind, tmdbId) ? logoImg(logo.url) : null;   // v350: stored logo: never TMDb's too
  if (logoUrl) void preloadImage(logoUrl, 'high');
  // A Home card's tap leaves its on-screen posters loading (v341r); a search row's open holds them (I1440).
  keepHomePostersOnOpen(!!card?.closest('[data-testid^="card-"]') && !card.closest(SEARCH_SURFACE + ',[role="dialog"]'));
  tapAt = now();
  beginSheetPriority();
  dropOtherWarms([ratingKey, tmdbId], card ? tapTitle(card) : undefined);
  const img = card?.querySelector('img') ?? null;
  const seed = colorsFromImage(img);
  if (seed) setSeedColors(kind, ratingKey, tmdbId, seed);
  shell = card ? { title: tapTitle(card), year: tapYear(card), poster: img?.currentSrc || img?.src || null, at: now(), owned: tapOwned(card, ratingKey), plex: !!ratingKey } : null;
  const cardWasConnected = !!card?.isConnected;
  if (card) { if (pressed && pressed !== card) release(); pressed = card; card.setAttribute('data-pressed', 'true'); holdFor(card, SHEET_OPEN_BOUND_MS); }
  // The art first (v341l): its request is queued before this open's detail calls, so a slow TMDb holding the
  // other connections never leaves it waiting for a socket (Tester I1440: art at +3.7 s behind TMDb calls).
  // The sheet then opens one task hop later (a MessageChannel yield: no timer, no network), so the press and warm-up
  // and the sheet's first render never share a task (at 4x CPU that single task was over 100 ms).
  for (const u of (art ?? [])) if (u) void preloadImage(u, 'high');
  void warmDetail(kind, ratingKey, tmdbId, { open: true, art, logo }).catch(() => {});
  await yieldToMain();
  if (mine !== openGen) return;
  // The tapped row left the page (back, unmount) during the art tick: do not open it later.
  if (card && cardWasConnected && !card.isConnected) {
    if (mine === openGen) { pending = false; release(card); endSheetPriority(); }
    return;
  }
  try {
    opened = { key: openKey, at: now() };
    open();
  } catch (e) {
    opened = null;
    if (mine === openGen) { pending = false; release(card); }
    throw e;
  }
  if (mine !== openGen) return;
  // The press is released by the sheet's first moving frame (SHEET_MOVING_EVENT); holdFor is the fallback.
  stopWatch = watchSheet(() => { if (mine !== openGen) return; pending = false; opened = null; onClosed?.(); });
}

export function _resetModalPrefetch(): void {
  cache.clear();
  _resetImageLoader();
  queue.length = 0;
  inflight.clear();
  owners.clear();
  _resetSheetPriority();
  started.clear();
  groupCount.clear();
  warmLoad = '';
  ratingsWarms = 0;
  searchRatingsWarms = 0;
  gen += 1;
  active = 0;
  pending = false;
  opened = null;
  tapAt = 0;
  shell = null;
  openGen += 1;
  stopWatch?.();
  stopWatch = null;
}
