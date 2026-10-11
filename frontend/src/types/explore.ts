/**
 * Unified tile model for Explore page smart tiles.
 * Each rail normalizes its source data into this shape so ExploreTile
 * can render decision-relevant info without knowing the data source.
 */
export interface ExploreTileModel {
  id: string;
  mediaType: 'movie' | 'tv';
  title: string;
  year: number | null;
  posterUrl: string | null;
  backdropUrl: string | null;
  clearLogoUrl?: string | null;

  // Decision signals
  traktWatchers?: number;   // Populated on Trending rails only
  rtCritic?: number;        // 0-100, undefined when unavailable
  rtAudience?: number;      // 0-100, undefined when unavailable
  isInPlex: boolean;
  isDownloadable: boolean;
  isInWatchlist: boolean;
  /** True once ownership/watchlist enrichment resolved; gates the status orb. */
  ownershipKnown?: boolean;
  isInTheaters: boolean;
  isNowAvailable: boolean;

  // TMDb or Trakt rating (0-10 scale)
  voteAverage: number | null;

  // IDs for modal routing
  ids: {
    tmdb?: number;
    trakt?: number;
    imdb?: string;
    tvdb?: number;
  };

  // Multi-user ownership context
  ownerType?: 'self' | 'shared';
  ownerName?: string;

  // Plex rating key for owned items
  ratingKey: number | null;

  overview: string | null;
}

/** Rail context — tells the normalizer which rail the items belong to. */
export type RailContext =
  | 'trakt_movies'
  | 'trakt_shows'
  | 'in_theaters'
  | 'now_available';
