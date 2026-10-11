import { useState, useEffect } from 'react';
import { apiFetch, mapExploreMovie } from '@/services/api';
import type { ExploreMovie, ExploreResponse } from '@/services/types';

export function useExploreMovies() {
  const [movies, setMovies] = useState<ExploreMovie[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchExplore() {
      try {
        const data = await apiFetch<ExploreResponse>('/api/explore/cached');
        if (!cancelled) {
          setMovies(data.movies.map(mapExploreMovie));
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load explore movies');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchExplore();

    return () => {
      cancelled = true;
    };
  }, []);

  return { movies, loading, error };
}
