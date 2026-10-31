/** IMDb (+ Rotten Tomatoes / Metacritic) via OMDb for TV detail, shared by the TV
 * Expanded and Compact (phone) layouts. Never TMDb. Off in the minimal build. */
import { useEffect, useState } from 'react';
import { apiFetch } from '@/apiClient';
import { isMinimal } from '@/minimal';
import type { Ratings } from '@/utils/modalParsers';

export interface ImdbRatingsState {
  ratings: Ratings | null; loading: boolean;
  /** The title the last answer was for (`settled` = answered for the current title). */
  doneFor?: string | null;
}

export function useImdbRatingsState(title: string | null | undefined, year: number | null | undefined): ImdbRatingsState {
  const [state, setState] = useState<ImdbRatingsState>({ ratings: null, loading: false });
  useEffect(() => {
    if (!title || isMinimal()) {
      setState({ ratings: null, loading: false, doneFor: title || null });
      return;
    }
    const controller = new AbortController();
    setState({ ratings: null, loading: true });
    const params = new URLSearchParams();
    if (year) params.set('year', String(year));
    apiFetch(`/api/ratings/${encodeURIComponent(title)}?${params}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (controller.signal.aborted) return;
        if (!d || d.error) { setState({ ratings: null, loading: false, doneFor: title }); return; }
        const imdb = (d.imdb as Ratings['imdb']) ?? null;
        const rtCritic = (d.rotten_tomatoes_critic as Ratings['rotten_tomatoes_critic']) ?? null;
        const rtAudience = (d.rotten_tomatoes_audience as Ratings['rotten_tomatoes_audience']) ?? null;
        const metacritic = (d.metacritic as Ratings['metacritic']) ?? null;
        if (!imdb?.score && !rtCritic?.score && !rtAudience?.score && !metacritic?.score) {
          setState({ ratings: null, loading: false, doneFor: title });
          return;
        }
        setState({ ratings: { imdb, rotten_tomatoes_critic: rtCritic, rotten_tomatoes_audience: rtAudience, metacritic, tmdb: null }, loading: false, doneFor: title });
      })
      .catch(() => { if (!controller.signal.aborted) setState({ ratings: null, loading: false, doneFor: title }); });
    return () => controller.abort();
  }, [title, year]);
  return state;
}

/** Whether the ratings answer is in for this title (a failure counts). */
export function ratingsDone(s: ImdbRatingsState, title: string | null | undefined): boolean {
  return !!title && !s.loading && s.doneFor === title;
}

export function useImdbRatings(title: string | null | undefined, year: number | null | undefined): Ratings | null {
  return useImdbRatingsState(title, year).ratings;
}
