/** The movie/TV sheet's ONE reveal (Taylor's rule): backdrop, logo or title, meta, ratings and buttons show
 *  together, in one fade, once. It opens when the details are in, the hero has latched (backdrop and logo decoded,
 *  or the deadline with what is ready, see useSheetHero) and the ratings row has latched or the deadline passed.
 *  Latched per open (`openKey`). The trailer button is decided at the reveal: known by then, or left out for
 *  that open, so the action row never re-flows. */
import { useEffect, useState } from 'react';

export interface OneReveal { revealed: boolean; trailer: boolean }
export function useOneReveal(openKey: unknown, detailsIn: boolean, heroLatched: boolean, ratingsShown: boolean,
  deadline: boolean, hasTrailer: boolean): OneReveal {
  const [st, setSt] = useState<{ key: unknown; trailer: boolean } | null>(null);
  const cur = st && st.key === openKey ? st : null;
  const ready = !cur && detailsIn && heroLatched && (ratingsShown || deadline);
  useEffect(() => { if (ready) setSt({ key: openKey, trailer: hasTrailer }); }, [ready, openKey, hasTrailer]);
  // The render that latches already shows everything (no frame with the details but no title).
  if (ready) return { revealed: true, trailer: hasTrailer };
  return { revealed: !!cur, trailer: cur ? cur.trailer : hasTrailer };
}
