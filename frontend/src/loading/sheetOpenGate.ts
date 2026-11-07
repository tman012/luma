/** A movie/TV detail sheet stays closed until its visible title and artwork are
 *  actually ready. An elapsed cap must not open a color wash or a partial sheet.
 *
 *  Bound: SHEET_OPEN_BOUND_MS (the existing 6s sheet-priority safety cap) from
 *  the moment the modal mounts. Past it, if the sheet is still not showable,
 *  the open is abandoned (onClose, nothing revealed). That is not a minimum
 *  delay: a warm title shows on the frame it becomes ready.
 *
 *  Showable:
 *  - Hard failure with no detail: the existing "Couldn't load details" panel.
 *  - Otherwise a real non-empty title, a settled hero (logo decoded, or the
 *    lookup/image failed so the text title is final; backdrop decoded, or art
 *    genuinely absent/failed so the no-art frame is final), and known ownership
 *    so the action row does not reflow.
 *  Ratings are not part of this gate. They fade into a reserved row.
 */
import { useEffect, useRef } from 'react';
import { SHEET_PRIORITY_MS } from '@/loading/sheetPriority';

export const SHEET_OPEN_BOUND_MS = SHEET_PRIORITY_MS;

const DETAIL_SHEET = '[data-modal="MovieDetailModal"], [data-modal="TvDetailModal"]';
const PERSON_SHEET = '[data-modal="PersonDetailModal"]';

/** True only when that surface is actually portaled. A mounted modal whose sheet is still
 *  closed is not a surface to return to. */
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

/** Closes an open that never became showable. `show` true (or a later true) cancels the timer. */
export function useUnopenedClose(openKey: unknown, show: boolean, onClose: () => void): void {
  const showRef = useRef(show);
  const closeRef = useRef(onClose);
  showRef.current = show;
  closeRef.current = onClose;
  useEffect(() => {
    if (show) return;
    const t = window.setTimeout(() => { if (!showRef.current) closeRef.current(); }, SHEET_OPEN_BOUND_MS);
    return () => window.clearTimeout(t);
  }, [openKey, show]);
}
