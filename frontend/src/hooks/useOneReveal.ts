/** The movie/TV sheet's ONE reveal (Taylor's rule): backdrop, logo or title, meta, ratings and buttons show
 *  together, in one fade, once. It opens when the details are in, the hero has latched (backdrop and logo decoded,
 *  or their deadlines, see useSheetHero), the ratings row has latched or the deadline passed, and the title's
 *  ownership is known (or the backdrop deadline passed). Latched per open (`openKey`). The button set is decided at
 *  the reveal: the trailer known by then (or left out for that open) and the ownership then, so the action row
 *  never re-flows. */
import { useEffect, useRef, useState } from 'react';
import { endSheetPriority } from '@/loading/sheetPriority';

export interface OneReveal { revealed: boolean; trailer: boolean; owned: boolean }
export function useOneReveal(openKey: unknown, detailsIn: boolean, heroLatched: boolean, ratingsShown: boolean,
  deadline: boolean, hasTrailer: boolean, owned = false, ownedKnown = true, artDeadline = false): OneReveal {
  const [st, setSt] = useState<{ key: unknown; trailer: boolean; owned: boolean } | null>(null);
  const cur = st && st.key === openKey ? st : null;
  const ready = !cur && detailsIn && heroLatched && (ratingsShown || deadline) && (ownedKnown || artDeadline);
  useEffect(() => { if (ready) setSt({ key: openKey, trailer: hasTrailer, owned }); }, [ready, openKey, hasTrailer, owned]);
  useEffect(() => { if (ready) endSheetPriority(); }, [ready]);
  // The render that latches already shows everything (no frame with the details but no title).
  if (ready) return { revealed: true, trailer: hasTrailer, owned };
  return cur ? { revealed: true, trailer: cur.trailer, owned: cur.owned } : { revealed: false, trailer: hasTrailer, owned };
}

/** The text the one reveal showed (meta, overview, seasons) stays for that open: a TMDb answer landing after the
 *  reveal (a capped open over Plex data) updates nothing visible. Other fields (ids, ownership, trailer) stay live. */
export function useRevealFreeze<T extends object>(v: T | null, revealed: boolean, openKey: unknown, keys: readonly (keyof T)[]): T | null {
  const snap = useRef<{ key: unknown; pick: Partial<T> } | null>(null);
  const memo = useRef<{ v: T; pick: Partial<T>; out: T } | null>(null);
  if (!revealed || !v) { if (snap.current && snap.current.key !== openKey) snap.current = null; return v; }
  if (!snap.current || snap.current.key !== openKey) {
    const pick: Partial<T> = {};
    for (const k of keys) pick[k] = v[k];
    snap.current = { key: openKey, pick };
  }
  const c = memo.current;
  if (c && c.v === v && c.pick === snap.current.pick) return c.out;
  const out = { ...v, ...snap.current.pick };
  memo.current = { v, pick: snap.current.pick, out };
  return out;
}
export const TV_TEXT = ['title', 'year', 'summary', 'contentRating', 'rating', 'genres', 'network', 'numberOfSeasons', 'numberOfEpisodes', 'status', 'inProduction', 'creators'] as const;
export const MOVIE_TEXT = ['title', 'year', 'summary', 'contentRating', 'rating', 'durationMs', 'genres', 'directors'] as const;
