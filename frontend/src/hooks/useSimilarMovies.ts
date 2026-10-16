/**
 * useSimilarMovies — "More Like This" data for the movie detail modal.
 *
 * Backend splits the list:
 *   - owned   = Plex Related Movies hub (exact same list as the Plex app)
 *   - unowned = Trakt related, minus anything already owned (TMDb fallback)
 *
 * Pass ratingKey when the source title is in the library so the owned rail
 * can hit Plex related directly; without it the backend still tries a GUID
 * resolve and will leave owned empty for pure-TMDb titles.
 */

import { useState, useEffect } from 'react';
import type { NormalizedMediaItem } from '@/types/media';
import { posterUrl } from '@/utils/image';
import { fetchWithTimeout } from '@/utils/fetchWithTimeout';

interface SimilarMoviesResult {
  owned: NormalizedMediaItem[];
  unowned: NormalizedMediaItem[];
  loading: boolean;
  error: boolean;
}

interface SimilarApiMovie {
  tmdb_id: number | null;
  title: string;
  year: number | null;
  poster_path: string | null;
  backdrop_path: string | null;
  overview: string;
  vote_average: number | null;
  imdb_rating?: number | null;
  rating?: number | null;
  rating_key?: number | null;
  owned?: boolean;
  source?: string;
}

const EMPTY: NormalizedMediaItem[] = [];

function mapMovie(m: SimilarApiMovie, owned: boolean): NormalizedMediaItem {
  return {
    id: `similar-${owned ? 'owned' : 'unowned'}-${m.tmdb_id ?? m.rating_key}`,
    title: m.title,
    year: m.year,
    posterUrl: posterUrl(m.poster_path, 'w342'),
    mediaType: 'movie' as const,
    tmdbId: m.tmdb_id,
    ratingKey: m.rating_key ?? null,
    owned,
    ownershipKnown: true,
    inWatchlist: false,
    overview: m.overview,
    voteAverage: m.imdb_rating ?? m.rating ?? null,
  };
}

export function useSimilarMovies(
  tmdbId: number | null | undefined,
  ratingKey?: number | null,
): SimilarMoviesResult {
  const [owned, setOwned] = useState<NormalizedMediaItem[]>(EMPTY);
  const [unowned, setUnowned] = useState<NormalizedMediaItem[]>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!tmdbId) {
      setOwned(EMPTY);
      setUnowned(EMPTY);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(false);

    (async () => {
      try {
        const params = new URLSearchParams({ limit: '24' });
        if (ratingKey) params.set('rating_key', String(ratingKey));
        const resp = await fetchWithTimeout(
          `/api/movie/${tmdbId}/similar?${params.toString()}`,
        );
        if (!resp.ok) throw new Error(String(resp.status));
        const data = await resp.json();
        if (cancelled) return;

        const ownedItems = ((data.owned ?? []) as SimilarApiMovie[])
          .filter((m) => m.tmdb_id || m.rating_key)
          .map((m) => mapMovie(m, true));
        const unownedItems = ((data.unowned ?? data.movies ?? []) as SimilarApiMovie[])
          .filter((m) => m.tmdb_id && !m.owned)
          .map((m) => mapMovie(m, false));

        // Back-compat: if the API still returns a flat movies list without
        // owned/unowned keys, treat everything as unowned rather than hanging.
        if (!data.owned && !data.unowned && Array.isArray(data.movies)) {
          setOwned(EMPTY);
          setUnowned(
            (data.movies as SimilarApiMovie[])
              .filter((m) => m.tmdb_id)
              .map((m) => mapMovie(m, false)),
          );
        } else {
          setOwned(ownedItems);
          setUnowned(unownedItems);
        }
        setLoading(false);
      } catch {
        if (cancelled) return;
        setError(true);
        setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [tmdbId, ratingKey]);

  return { owned, unowned, loading, error };
}
