/**
 * Normalizer utilities for converting enriched media items into ExploreTileModel[].
 * Keeps data-fetching logic decoupled from presentation.
 */

import type { NormalizedMediaItem } from '@/types/media';
import type { ExploreTileModel, RailContext } from '@/types/explore';

/**
 * Convert enriched media items to ExploreTileModel[] with rail-specific context flags.
 * The `context` param determines which contextual signals (isInTheaters, isNowAvailable, etc.)
 * are set on each tile.
 */
export function normalizeForTiles(
  items: NormalizedMediaItem[],
  context: RailContext,
): ExploreTileModel[] {
  return items.map((item) => ({
    id: item.id,
    mediaType: item.mediaType as 'movie' | 'tv',
    title: item.title,
    year: item.year,
    posterUrl: item.posterUrl,
    backdropUrl: item.backdropUrl ?? null,
    clearLogoUrl: item.clearLogoUrl,
    traktWatchers: item.traktWatchers,
    rtCritic: undefined,
    rtAudience: undefined,
    isInPlex: item.owned,
    isDownloadable: context === 'now_available',
    isInWatchlist: item.inWatchlist,
    ownershipKnown: item.ownershipKnown,
    ownerType: item.ownerType,
    ownerName: item.ownerName,
    isInTheaters: context === 'in_theaters',
    isNowAvailable: context === 'now_available',
    voteAverage: item.voteAverage ?? null,
    ids: {
      tmdb: item.tmdbId ?? undefined,
      tvdb: item.tvdbId ?? undefined,
      imdb: item.imdbId ?? undefined,
    },
    ratingKey: item.ratingKey,
    overview: item.overview ?? null,
  }));
}
