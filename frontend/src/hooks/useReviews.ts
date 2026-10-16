import { useState, useEffect } from 'react';
import type { ReviewsData } from '@/types/reviews';
import { apiFetch } from '../apiClient';

interface UseReviewsReturn {
  data: ReviewsData | null;
  loading: boolean;
  error: boolean;
}

/**
 * Fetches Rotten Tomatoes reviews for a given title and year.
 * Handles AbortController cleanup on unmount.
 * @param mediaType - 'movie' or 'tv' (defaults to 'movie')
 */
export function useReviews(title: string, year: number | null, mediaType: 'movie' | 'tv' = 'movie'): UseReviewsReturn {
  const [data, setData] = useState<ReviewsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    // Don't fetch with empty title — detail hasn't loaded yet
    if (!title) {
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    let cancelled = false;

    async function fetchReviews() {
      setLoading(true);
      setError(false);
      setData(null);

      try {
        const params = new URLSearchParams({ title });
        if (year != null) params.set('year', String(year));
        params.set('limit', '100');
        if (mediaType === 'tv') params.set('media_type', 'tv');

        const resp = await apiFetch(`/api/rt/reviews?${params.toString()}`, {
          signal: controller.signal,
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const result = (await resp.json()) as ReviewsData;
        if (!cancelled) setData(result);
      } catch (err: unknown) {
        if (!cancelled && (err as { name?: string })?.name !== 'AbortError') {
          setError(true);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchReviews();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [title, year, mediaType]);

  return { data, loading, error };
}
