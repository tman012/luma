/** Detail sheet open helpers.
 *
 *  A movie/TV sheet opens on the tap into its final frame and never waits for the network: the details, the logo
 *  and the artwork reveal into it once they are ready (useOneReveal), and an open that cannot load shows the
 *  existing "Couldn't load details" panel. `detailSheetReady` therefore only says whether the sheet is composed
 *  (real title, settled hero, known ownership); it no longer decides whether the sheet is shown.
 *
 *  `SHEET_OPEN_BOUND_MS` is the sheet-priority safety cap (loading/sheetPriority). Ratings are not part of
 *  composed: they fade into a reserved row.
 */
import { useEffect } from 'react';
import { SHEET_PRIORITY_MS } from '@/loading/sheetPriority';

export const SHEET_OPEN_BOUND_MS = SHEET_PRIORITY_MS;

const NOT_EXITING = ':not([data-phase="exiting"])';
const DETAIL_SHEET = `[data-modal="MovieDetailModal"]${NOT_EXITING}, [data-modal="TvDetailModal"]${NOT_EXITING}`;
const PERSON_SHEET = `[data-modal="PersonDetailModal"]${NOT_EXITING}`;

/** True when that surface is portaled and not on its way out (an exiting sheet is not a surface to return to). */
export function detailSurfaceOpen(which: 'detail' | 'person' = 'detail'): boolean {
  if (typeof document === 'undefined') return false;
  return !!document.querySelector(which === 'person' ? PERSON_SHEET : DETAIL_SHEET);
}

export function detailSheetReady(
  loading: boolean, error: boolean, hasDetail: boolean, title: string | null | undefined,
  hero: 'hold' | 'logo' | 'text', ownedKnown = true,
): boolean {
  if (!loading && error && !hasDetail) return true;
  return !loading && hasDetail && !!title && title.trim().length > 0 && hero !== 'hold' && ownedKnown;
}

/** `html[data-detail-mounted]` while at least one detail sheet is mounted (a parent sheet stays mounted under a child). */
let mountedDetails = 0;
export function useDetailMounted(): void {
  useEffect(() => {
    mountedDetails += 1;
    document.documentElement.dataset.detailMounted = '1';
    return () => {
      mountedDetails -= 1;
      if (mountedDetails <= 0) { mountedDetails = 0; delete document.documentElement.dataset.detailMounted; }
    };
  }, []);
}
