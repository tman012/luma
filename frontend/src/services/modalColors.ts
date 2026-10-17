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

function cached(url: string): Promise<UltraBlurColors | null> {
  let p = cache.get(url);
  if (!p) {
    p = apiFetch(url)
      .then(r => (r.ok ? (r.json() as Promise<UltraBlurColors>) : null))
      .then(c => (has(c) ? c : null))
      .catch(() => null);
    cache.set(url, p);
    if (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value as string);
    // A miss (offline, no key) may work later: don't keep it.
    p.then(c => { if (!c) cache.delete(url); });
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
  seeds.clear();
}
