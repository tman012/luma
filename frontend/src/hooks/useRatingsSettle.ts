/** Movie/TV sheet ratings as ONE settled row (Taylor, 2026-10-31): the badges appear together, once.
 *
 * - `value` is the merged ratings so far; `settled` is true once every source for this open has answered
 *   (a failed or empty source counts as answered).
 * - The row latches when the sources have settled and the badge icons have decoded, or RATINGS_CAP_MS after the
 *   details landed (`active`; the ratings fetches start then), whichever comes first. At the cap whatever is ready
 *   shows, together. If nothing is in hand at the cap the row does not lock empty: it reveals the first non-empty
 *   answer when it arrives (one fade), or the reserved empty row once every source has answered empty or failed.
 * - Both reveal paths wait for the badge icons to decode, for at most RATINGS_ICON_CAP_MS once the data is ready,
 *   so the icons paint with the text and a stuck icon can't hold the row.
 * - Once latched the row never changes for that open (`openKey`): a source that answers later is dropped. */
import { useEffect, useMemo, useState } from 'react';
import type { Ratings } from '@/utils/modalParsers';
import { buildRatingItems, decodeRatingIcon, ratingIconDecoded } from '@/utils/ratingIcons';

export const RATINGS_CAP_MS = 1000;
export const RATINGS_ICON_CAP_MS = 300;


/** Decodes the badge icons the ratings need (shared with the boot preload); true once all have decoded or failed. */
function useIconsReady(icons: string[]): boolean {
  const key = icons.join('|');
  const [done, setDone] = useState<string | null>(null);
  const ready = !key || done === key || icons.every(ratingIconDecoded);
  useEffect(() => {
    if (!key || ready) return;
    let live = true;
    Promise.all(key.split('|').map(decodeRatingIcon)).then(() => { if (live) setDone(key); });
    return () => { live = false; };
  }, [key, ready]);
  return ready;
}

export interface RatingsSettleState { ratings: Ratings | null; shown: boolean }
interface St { key: unknown; capped: boolean; shown: boolean; ratings: Ratings | null; iconWait?: boolean }

export function useRatingsSettle(value: Ratings | null | undefined, settled: boolean, openKey: unknown, active = true): RatingsSettleState {
  const [st, setSt] = useState<St>({ key: openKey, capped: false, shown: false, ratings: null });
  const cur: St = st.key === openKey ? st : { key: openKey, capped: false, shown: false, ratings: null };
  const icons = useMemo(() => (value ? [...new Set(buildRatingItems(value).map(i => i.icon))] : []), [value]);
  const iconsReady = useIconsReady(icons);
  useEffect(() => {
    setSt(s => (s.key === openKey ? s : { key: openKey, capped: false, shown: false, ratings: null }));
  }, [openKey]);
  // The cap counts from when the details land (the ratings fetches start then), once per open.
  useEffect(() => {
    if (!active) return;
    const t = window.setTimeout(() => setSt(s => (s.key === openKey && !s.capped ? { ...s, capped: true } : s)), RATINGS_CAP_MS);
    return () => window.clearTimeout(t);
  }, [openKey, active]);
  const has = !!value && buildRatingItems(value).length > 0;
  // Settled: lock with whatever is there, empty included. Capped: lock only with something to show.
  const dataReady = !cur.shown && (settled || (cur.capped && has));
  useEffect(() => {
    if (!dataReady || iconsReady) return;
    const t = window.setTimeout(() => setSt(s => (s.key === openKey && !s.iconWait ? { ...s, iconWait: true } : s)), RATINGS_ICON_CAP_MS);
    return () => window.clearTimeout(t);
  }, [dataReady, iconsReady, openKey]);
  useEffect(() => {
    if (dataReady && (iconsReady || cur.iconWait)) {
      setSt({ ...cur, capped: true, shown: true, ratings: has ? value! : null });
    }
  }, [dataReady, iconsReady, value, has, cur]);
  return { ratings: cur.ratings, shown: cur.shown };
}
