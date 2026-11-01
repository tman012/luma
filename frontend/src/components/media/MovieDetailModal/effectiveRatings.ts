/** Movie detail ratings (web and phone alike): IMDb and Metacritic from the detail's ratings, Rotten
 * Tomatoes critic/audience from them too, else from the RT reviews endpoint. Was web-only (ExpandedLayout);
 * phone (CompactLayout) showed only the raw ratings, so a movie whose ratings lacked RT lost it on phone. */
import { useMemo } from 'react';
import type { Ratings } from '@/utils/modalParsers';
import { useReviews } from '@/hooks/useReviews';

export function mergeRatings(ratings: Ratings | null | undefined,
  reviews: { critic_score?: number | null; audience_score?: number | null } | null | undefined): Ratings | null {
  const rtCritic = ratings?.rotten_tomatoes_critic?.score != null ? ratings.rotten_tomatoes_critic
    : reviews?.critic_score != null ? { score: reviews.critic_score } : null;
  const rtAudience = ratings?.rotten_tomatoes_audience?.score != null ? ratings.rotten_tomatoes_audience
    : reviews?.audience_score != null ? { score: reviews.audience_score } : null;
  const imdb = ratings?.imdb ?? null;
  const metacritic = ratings?.metacritic ?? null;
  const tmdb = ratings?.tmdb ?? null;
  if (!imdb?.score && !rtCritic?.score && !rtAudience?.score && !metacritic?.score && !tmdb?.score) return null;
  return { imdb, rotten_tomatoes_critic: rtCritic, rotten_tomatoes_audience: rtAudience, metacritic, tmdb } as Ratings;
}

export function useEffectiveRatingsState(ratings: Ratings | null | undefined, title: string | null | undefined, year: number | null | undefined,
  inline = false): { ratings: Ratings | null; settled: boolean } {
  // Only ask the reviews endpoint when the ratings are missing an RT score (no extra request otherwise); ratings
  // that came inline with the details already include what RT had within the server's budget.
  const needRt = !inline && (ratings?.rotten_tomatoes_critic?.score == null || ratings?.rotten_tomatoes_audience?.score == null);
  const q = useReviews(needRt ? (title ?? '') : '', year ?? null);
  const merged = useMemo(() => mergeRatings(ratings, q.data), [ratings, q.data]);
  // Settled: no RT fill needed, or the reviews request has answered (a failure counts).
  return { ratings: merged, settled: !needRt || (!!title && !q.loading) };
}

export function useEffectiveRatings(ratings: Ratings | null | undefined, title: string | null | undefined, year: number | null | undefined,
  inline = false): Ratings | null {
  return useEffectiveRatingsState(ratings, title, year, inline).ratings;
}
