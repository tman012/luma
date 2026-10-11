/**
 * Canonical ownership service — single source of truth for "In Library" resolution.
 *
 * Provides a session-level cache so navigating between Explore / Trending / Mobile
 * doesn't re-fetch ownership for items already checked.
 */

import { batchCheckOwnership } from '@/services/searchApi';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MediaRef {
  title: string;
  year: number | null;
  tmdbId: number | null;
  ratingKey: number | null;
  mediaType?: 'movie' | 'tv' | 'episode';
  tvdbId?: number | null;
  imdbId?: string | null;
}

interface OwnedResult {
  owned: boolean;
  ratingKey: number | null;
  ownerType?: 'self' | 'shared';
  ownerName?: string;
}

// ---------------------------------------------------------------------------
// Cache key — matches backend's "<type>:title_lower_year" format. The media
// type prefix prevents a movie and a same-title/same-year show from colliding
// (both in this session cache and in the batch API result dict).
// ---------------------------------------------------------------------------

export function mediaKey(ref: {
  title: string;
  year: number | null;
  mediaType?: 'movie' | 'tv' | 'episode';
}): string {
  const type = ref.mediaType === 'tv' || ref.mediaType === 'episode' ? 'tv' : 'movie';
  return `${type}:${ref.title.toLowerCase()}_${ref.year ?? ''}`;
}

// ---------------------------------------------------------------------------
// Module-level cache (short TTL so a grab/import mid-session isn't stuck
// showing "not in library" until a full reload).
// ---------------------------------------------------------------------------

const OWNERSHIP_TTL = 60_000; // 60s — matches the ownership-accuracy guardrail
const _cache = new Map<string, { entry: OwnedResult; ts: number }>();

// ---------------------------------------------------------------------------
// Core resolver
// ---------------------------------------------------------------------------

export async function resolveOwnership(
  refs: MediaRef[],
): Promise<Map<string, OwnedResult>> {
  const result = new Map<string, OwnedResult>();
  const toFetch: MediaRef[] = [];

  for (const ref of refs) {
    const key = mediaKey(ref);

    // Fast path: ratingKey present → already owned
    if (ref.ratingKey) {
      const entry: OwnedResult = { owned: true, ratingKey: ref.ratingKey };
      _cache.set(key, { entry, ts: Date.now() });
      result.set(key, entry);
      continue;
    }

    // Cache hit (within TTL)
    const cached = _cache.get(key);
    if (cached !== undefined && Date.now() - cached.ts < OWNERSHIP_TTL) {
      result.set(key, cached.entry);
      continue;
    }

    // Needs API check
    toFetch.push(ref);
  }

  // Batch API call for uncached items
  if (toFetch.length > 0) {
    try {
      // Split by mediaType so backend queries correct Plex library sections
      const movieRefs = toFetch.filter((r) => !r.mediaType || r.mediaType === 'movie');
      // Episodes resolve ownership at the SHOW level (same Plex TV section).
      const tvRefs = toFetch.filter((r) => r.mediaType === 'tv' || r.mediaType === 'episode');

      const payload: Parameters<typeof batchCheckOwnership>[0] = {};
      if (movieRefs.length > 0) {
        payload.movies = movieRefs.map((r) => ({
          title: r.title,
          year: r.year,
          tmdb_id: r.tmdbId!,
        }));
      }
      if (tvRefs.length > 0) {
        payload.tv = tvRefs.map((r) => ({
          title: r.title,
          year: r.year,
          tmdb_id: r.tmdbId!,
          ...(r.tvdbId ? { tvdb_id: r.tvdbId } : {}),
          ...(r.imdbId ? { imdb_id: r.imdbId } : {}),
        }));
      }

      const apiResults = await batchCheckOwnership(payload);

      for (const ref of toFetch) {
        const key = mediaKey(ref);
        const match = apiResults[key];
        const entry: OwnedResult = match?.owned
          ? {
              owned: true,
              ratingKey: match.rating_key ?? null,
              ownerType: (match as any).owner_type as OwnedResult['ownerType'],
              ownerName: (match as any).owner_name as string | undefined,
            }
          : { owned: false, ratingKey: null };
        _cache.set(key, { entry, ts: Date.now() });
        result.set(key, entry);
      }
    } catch {
      // On error, mark all unfetched as not-owned (don't cache failures)
      for (const ref of toFetch) {
        const key = mediaKey(ref);
        result.set(key, { owned: false, ratingKey: null });
      }
    }
  }

  return result;
}

// ---------------------------------------------------------------------------
// Convenience wrapper — drop-in replacement for inline enrichOwnership()
// ---------------------------------------------------------------------------

export async function enrichOwnership<T extends {
  title: string;
  year: number | null;
  tmdbId: number | null;
  ratingKey: number | null;
  owned: boolean;
  mediaType?: 'movie' | 'tv' | 'episode';
  tvdbId?: number | null;
  imdbId?: string | null;
}>(items: T[]): Promise<T[]> {
  const toCheck = items.filter((i) => !i.owned && i.tmdbId);
  if (toCheck.length === 0) return items;

  const ownershipMap = await resolveOwnership(toCheck);

  return items.map((item) => {
    if (item.owned || !item.tmdbId) return item;
    const key = mediaKey(item);
    const match = ownershipMap.get(key);
    if (match?.owned) {
      const isEpisode = item.mediaType === 'episode';
      return {
        ...item,
        owned: true,
        // For episodes, ratingKey on the item is the SHOW key (modal open
        // uses showRatingKey ?? ratingKey). Preserve Continue Watching's
        // episode-level ratingKey if already set.
        ratingKey: isEpisode && item.ratingKey ? item.ratingKey : match.ratingKey,
        ...(isEpisode ? { showRatingKey: (item as { showRatingKey?: number | null }).showRatingKey ?? match.ratingKey } : {}),
        ownerType: match.ownerType,
        ownerName: match.ownerName,
      };
    }
    return item;
  });
}
