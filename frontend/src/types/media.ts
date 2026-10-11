/**
 * Unified media item type used by all rail hooks (Explore + Home).
 *
 * Fields shared by every rail are required; fields specific to a single
 * source (tracker metadata, continue-watching progress, episode context)
 * are optional.
 */
export interface NormalizedMediaItem {
  id: string;
  title: string;
  year: number | null;
  posterUrl: string | null;
  backdropUrl?: string | null;
  mediaType: 'movie' | 'tv' | 'episode';
  tmdbId: number | null;
  ratingKey: number | null;
  owned: boolean;
  inWatchlist: boolean;
  /**
   * True once ownership/watchlist enrichment has resolved for this item.
   * Undefined/false means "not determined yet" — UI should NOT render a
   * status orb (avoids a misleading red/unowned flash before enrichment,
   * and suppresses orbs on library rails that never enrich).
   */
  ownershipKnown?: boolean;

  // Multi-user ownership context
  ownerType?: 'self' | 'shared';
  ownerName?: string;

  // Optional IDs
  tvdbId?: number | null;
  imdbId?: string | null;

  // Metadata (optional — not every source provides these)
  overview?: string | null;
  voteAverage?: number | null;
  clearLogoUrl?: string | null;
  traktWatchers?: number;
  subtitle?: string;

  /** Compact US digital release badge e.g. "JAN 9" (from /api/watchlist). */
  digitalReleaseDate?: string | null;
  digitalReleaseLabel?: string | null;

  // Continue-watching / episode context
  showRatingKey?: number | null;
  progressPercent?: number;
  seasonNumber?: number | null;
  episodeNumber?: number | null;
  episodeRatingKey?: number | null;

  // Tracker-specific (populated for now_available items)
  resolution?: string | null;
  source?: string | null;
  tracker?: string | null;
  seeders?: number;
  sizeGb?: number | null;
  pubDate?: string | null;
  dateAdded?: string | null;
  createdAt?: string | null;
  torrentName?: string | null;
}

export interface ModalTarget {
  tmdbId?: number;
  ratingKey?: number;
  mediaType?: 'movie' | 'tv';
}
