import type { RawHeroMovie, HeroMovie, RawExploreMovie, ExploreMovie } from './types';
import { posterUrl, backdropUrl } from '@/utils/image';
import { apiFetch as baseApiFetch } from '../apiClient';

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function apiFetch<T>(
  endpoint: string,
  options?: RequestInit,
): Promise<T> {
  const response = await baseApiFetch(endpoint, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    ...options,
  });

  if (!response.ok) {
    throw new ApiError(response.status, `API error: ${response.statusText}`);
  }

  return response.json() as Promise<T>;
}

/** Map a raw snake_case hero movie from the API to camelCase. */
export function mapHeroMovie(raw: RawHeroMovie): HeroMovie {
  return {
    ratingKey: raw.rating_key,
    title: raw.title,
    year: raw.year ?? null,
    tmdbId: raw.tmdb_id ?? null,
    imdbId: raw.imdb_id ?? null,
    summary: raw.summary ?? '',
    contentRating: raw.content_rating ?? '',
    duration: raw.duration ?? 0,
    rating: raw.rating ?? raw.rt_score ?? null,
    audienceRating: raw.audience_rating ?? raw.rt_score ?? null,
    genres: raw.genres ?? [],
    posterUrl: raw.poster_url ?? null,
    backdropUrl: raw.backdrop_url ?? null,
    tmdbBackdropUrl: raw.tmdb_backdrop_url ?? null,
    clearlogoUrl: raw.clearlogo_url ?? null,
    addedAt: raw.added_at ?? 0,
    addedDate: raw.added_date ?? null,
    libraryName: raw.library_name ?? '',
  };
}

/** Map a raw snake_case explore movie to camelCase. Explore uses TMDB paths. */
export function mapExploreMovie(raw: RawExploreMovie): ExploreMovie {
  return {
    title: raw.title,
    year: raw.year,
    tmdbId: raw.tmdb_id,
    posterUrl: posterUrl(raw.poster_path),
    backdropUrl: backdropUrl(raw.backdrop_path),
    seeders: raw.seeders,
    sizeBytes: raw.size_bytes,
    resolution: raw.resolution ?? null,
    tracker: raw.tracker ?? null,
    publishDate: raw.publish_date ?? null,
    imdbId: raw.imdb_id ?? null,
    overview: raw.overview ?? null,
  };
}
