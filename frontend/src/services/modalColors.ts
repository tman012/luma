/** Detail popup color blur (UltraBlur palette), fetched once and cached, so it can be warmed from a card hover/tap before the popup opens
 * and is ready when the backdrop fades in. TMDB colors (they match the HD
 * backdrop) win when they arrive within TMDB_PREFER_MS; else Plex's. */
import { apiFetch } from '@/apiClient';
import type { UltraBlurColors } from '@/utils/modalParsers';

export type ColorKind = 'movie' | 'tv';
export const TMDB_PREFER_MS = 400;
const MAX_ENTRIES = 300;
// Cached per source URL, so a card that only knows its Plex key (a TV show
// before its TMDb id is known) still warms what the popup will ask for.
const cache = new Map<string, Promise<UltraBlurColors | null>>();

const has = (c: UltraBlurColors | null | undefined): c is UltraBlurColors =>
  !!c && !!(c.topLeft || c.topRight || c.bottomRight || c.bottomLeft);

/** A definite "no palette" answer (HTTP error or an empty palette) is kept
 *  this long, so one popup open asks once instead of once per caller; a
 *  network failure isn't kept at all. */
export const MISS_TTL_MS = 60_000;
const missAt = new Map<string, number>();

/** Palettes that have already answered (null: a definite miss), so a hook can read one on its first render. */
const answered = new Map<string, UltraBlurColors | null>();

/** Palette requests in flight (abortable): a tap drops other titles' ones so they never hold the connections the
 *  opened title's art needs (v341l: four background /api/tmdb/colors calls behind a slow TMDb starved the art). */
const inflight = new Map<string, AbortController | null>();
export function dropOtherColors(keepIds: number[]): void {
  const keep = keepIds.map((x) => new RegExp(`/${x}(?:[/?&]|$)`));
  for (const [url, ac] of Array.from(inflight)) {
    if (keep.some((re) => re.test(url))) continue;
    inflight.delete(url); cache.delete(url); missAt.delete(url); answered.delete(url); ac?.abort();
  }
}

function cached(url: string): Promise<UltraBlurColors | null> {
  let p = cache.get(url);
  const miss = missAt.get(url);
  if (p && miss !== undefined && Date.now() - miss > MISS_TTL_MS) {
    cache.delete(url); missAt.delete(url); answered.delete(url); p = undefined;
  }
  if (!p) {
    const ac = typeof AbortController === 'undefined' ? null : new AbortController();
    if (ac) { inflight.set(url, ac); }
    const done = () => { if (inflight.get(url) === ac) inflight.delete(url); };
    p = apiFetch(url, ac ? { signal: ac.signal } : undefined)
      .then(r => (r.ok ? (r.json() as Promise<UltraBlurColors>) : null))
      .then(c => {
        if (has(c)) { answered.set(url, c); return c; }
        missAt.set(url, Date.now()); // answered, but nothing to show
        answered.set(url, null);
        return null;
      }, () => {
        if (cache.get(url) === p) cache.delete(url); // offline / aborted: may work on the next try
        return null;
      });
    p.then(done, done);
    cache.set(url, p);
    if (cache.size > MAX_ENTRIES) {
      const k = cache.keys().next().value as string;
      cache.delete(k); missAt.delete(k); answered.delete(k);
    }
  }
  return p;
}

const tmdbUrl = (kind: ColorKind, id: number) => `/api/tmdb/colors/${id}${kind === 'tv' ? '?type=tv' : ''}`;
const plexUrl = (rk: number) => `/api/plex/ultrablur/${rk}`;

/** The palette for an item; null when neither source has one. */
export async function modalColors(kind: ColorKind, ratingKey?: number, tmdbId?: number): Promise<UltraBlurColors | null> {
  const tmdbP = tmdbId ? cached(tmdbUrl(kind, tmdbId)) : Promise.resolve(null);
  const plexP = ratingKey ? cached(plexUrl(ratingKey)) : Promise.resolve(null);
  if (!tmdbId) return plexP;
  if (!ratingKey) return tmdbP;
  let timer = 0;
  const early = await Promise.race([
    tmdbP,
    new Promise<undefined>(res => { timer = window.setTimeout(() => res(undefined), TMDB_PREFER_MS); }),
  ]);
  window.clearTimeout(timer);
  if (early) return early;
  const plex = await plexP;
  if (plex) return plex;
  return early === undefined ? tmdbP : null;
}

/** The palette `modalColors` would answer right now without waiting, or undefined while it is still being fetched.
 *  Lets a sheet that opens after the warm-up finished start on the final palette, never on a placeholder. */
export function peekModalColors(kind: ColorKind, ratingKey?: number, tmdbId?: number): UltraBlurColors | null | undefined {
  const tmdb = tmdbId ? answered.get(tmdbUrl(kind, tmdbId)) : undefined;
  const plex = ratingKey ? answered.get(plexUrl(ratingKey)) : undefined;
  if (!tmdbId) return ratingKey ? plex : undefined;
  if (!ratingKey) return tmdb;
  if (tmdb) return tmdb;
  return tmdb === null ? plex : undefined;
}

/** Warm the palette from a card (hover, focus, tap) before the popup opens. */
export function prefetchModalColors(kind: ColorKind, ratingKey?: number, tmdbId?: number): void {
  if (tmdbId) void cached(tmdbUrl(kind, tmdbId));
  if (ratingKey) void cached(plexUrl(ratingKey));
}

// Poster-derived palettes set by the tap that opens a popup (phone), read by
// the popup on its first render so its first paint is already the blur.
const seeds = new Map<string, UltraBlurColors>();
const seedKey = (kind: ColorKind, ratingKey?: number, tmdbId?: number) =>
  ratingKey ? `${kind}:rk:${ratingKey}` : `${kind}:tmdb:${tmdbId}`;

export function setSeedColors(kind: ColorKind, ratingKey: number | undefined, tmdbId: number | undefined, c: UltraBlurColors): void {
  if (!ratingKey && !tmdbId) return;
  seeds.set(seedKey(kind, ratingKey, tmdbId), c);
  if (seeds.size > 50) seeds.delete(seeds.keys().next().value as string);
}

export function getSeedColors(kind: ColorKind, ratingKey?: number, tmdbId?: number): UltraBlurColors | null {
  return (ratingKey && seeds.get(seedKey(kind, ratingKey))) || (tmdbId && seeds.get(seedKey(kind, undefined, tmdbId))) || null;
}

export function _resetModalColors(): void {
  cache.clear();
  missAt.clear();
  answered.clear();
  seeds.clear();
}
