/** The movie/TV sheet's ONE reveal (Taylor's rule): backdrop, logo or title, meta, ratings and buttons show
 *  together, in one fade, once. It opens when the details are in, the hero has latched (backdrop and logo decoded,
 *  or their deadlines, see useSheetHero), the ratings row has latched or the deadline passed, and the title's
 *  ownership is known (or the backdrop deadline passed). Latched per open (`openKey`). The button set is decided at
 *  the reveal: the trailer known by then (or left out for that open) and the ownership then, so the action row
 *  never re-flows. */
import { useEffect, useState } from 'react';

export interface OneReveal { revealed: boolean; trailer: boolean; owned: boolean }
export function useOneReveal(openKey: unknown, detailsIn: boolean, heroLatched: boolean, ratingsShown: boolean,
  deadline: boolean, hasTrailer: boolean, owned = false, ownedKnown = true, artDeadline = false): OneReveal {
  const [st, setSt] = useState<{ key: unknown; trailer: boolean; owned: boolean } | null>(null);
  const cur = st && st.key === openKey ? st : null;
  const ready = !cur && detailsIn && heroLatched && (ratingsShown || deadline) && (ownedKnown || artDeadline);
  useEffect(() => { if (ready) setSt({ key: openKey, trailer: hasTrailer, owned }); }, [ready, openKey, hasTrailer, owned]);
  // The render that latches already shows everything (no frame with the details but no title).
  if (ready) return { revealed: true, trailer: hasTrailer, owned };
  return cur ? { revealed: true, trailer: cur.trailer, owned: cur.owned } : { revealed: false, trailer: hasTrailer, owned };
}
