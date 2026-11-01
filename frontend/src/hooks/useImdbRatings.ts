/** IMDb (+ Rotten Tomatoes / Metacritic) for TV detail, shared by the TV Expanded and Compact (phone) layouts.
 * Never TMDb. `inline`: the ratings the detail reply carried (the server asks every source within its budget,
 * the minimal build included); only a reply without them falls back to the OMDb endpoint (off in minimal). */
import { useEffect, useState } from 'react';
import { detailFetch, titleRatingsPath } from '@/services/modalPrefetch';
import { isMinimal } from '@/minimal';
import type { Ratings } from '@/utils/modalParsers';

export interface ImdbRatingsState { ratings: Ratings | null; loading: boolean }

function nonEmpty(r: Ratings | null | undefined): Ratings | null {
  return r && [r.imdb, r.rotten_tomatoes_critic, r.rotten_tomatoes_audience, r.metacritic].some((x) => x?.score != null)
    ? { ...r, tmdb: null } : null;
}

export function useImdbRatingsState(title: string | null | undefined, year: number | null | undefined,
  inline?: Ratings | null): ImdbRatingsState {
  const [state, setState] = useState<ImdbRatingsState>({ ratings: null, loading: false });
  const hasInline = inline !== undefined;
  useEffect(() => {
    if (hasInline) {
      setState({ ratings: nonEmpty(inline), loading: false });
      return;
    }
    if (!title || isMinimal()) {
      setState({ ratings: null, loading: false });
      return;
    }
    const controller = new AbortController();
    setState({ ratings: null, loading: true });
    detailFetch(titleRatingsPath(title, year), { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (controller.signal.aborted) return;
        if (!d || d.error) { setState({ ratings: null, loading: false }); return; }
        const imdb = (d.imdb as Ratings['imdb']) ?? null;
        const rtCritic = (d.rotten_tomatoes_critic as Ratings['rotten_tomatoes_critic']) ?? null;
        const rtAudience = (d.rotten_tomatoes_audience as Ratings['rotten_tomatoes_audience']) ?? null;
        const metacritic = (d.metacritic as Ratings['metacritic']) ?? null;
        if (!imdb?.score && !rtCritic?.score && !rtAudience?.score && !metacritic?.score) {
          setState({ ratings: null, loading: false });
          return;
        }
        setState({ ratings: { imdb, rotten_tomatoes_critic: rtCritic, rotten_tomatoes_audience: rtAudience, metacritic, tmdb: null }, loading: false });
      })
      .catch(() => { if (!controller.signal.aborted) setState({ ratings: null, loading: false }); });
    return () => controller.abort();
  }, [title, year, hasInline, inline]);
  return hasInline ? { ratings: nonEmpty(inline), loading: false } : state;
}

export function useImdbRatings(title: string | null | undefined, year: number | null | undefined, inline?: Ratings | null): Ratings | null {
  return useImdbRatingsState(title, year, inline).ratings;
}
