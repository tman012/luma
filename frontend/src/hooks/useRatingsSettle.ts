/** Movie/TV sheet ratings as ONE settled row (Taylor, 2026-10-31): the badges appear together, once.
 *
 * - `value` is the merged ratings so far; `settled` is true once every source for this open has answered
 *   (a failed or empty source counts as answered).
 * - The row latches when the sources have settled and the badge icons have decoded, or RATINGS_CAP_MS after the
 *   details landed (`active`; the ratings fetches start then), whichever comes first. At the cap whatever is ready
 *   shows, together. If nothing is in hand at the cap the row does not lock empty: it reveals the first non-empty
 *   answer when it arrives (one fade), or the reserved empty row once every source has answered empty or failed.
 * - Once latched the row never changes for that open (`openKey`): a source that answers later is dropped. */
import { useEffect, useMemo, useState } from 'react';
import type { Ratings } from '@/utils/modalParsers';
import { buildRatingItems } from '@/utils/ratingIcons';

export const RATINGS_CAP_MS = 1000;


/** Decodes the badge icons the ratings need; true once all have decoded (or failed). */
function useIconsReady(icons: string[]): boolean {
  const key = icons.join('|');
  const [done, setDone] = useState<string | null>(icons.length ? null : '');
  useEffect(() => {
    if (!key) { setDone(''); return; }
    let live = true;
    const imgs = key.split('|').map(u => { const im = new Image(); im.src = u; return im; });
    Promise.all(imgs.map(im => (typeof im.decode === 'function' ? im.decode() : Promise.resolve()).catch(() => {})))
      .then(() => { if (live) setDone(key); });
    return () => { live = false; };
  }, [key]);
  return done === key;
}

export interface RatingsSettleState { ratings: Ratings | null; shown: boolean }
interface St { key: unknown; capped: boolean; shown: boolean; ratings: Ratings | null }

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
  useEffect(() => {
    if (cur.shown) return;
    const has = !!value && buildRatingItems(value).length > 0;
    // Settled (icons decoded): lock with whatever is there, empty included. Capped: lock only with something to show.
    if ((settled && iconsReady) || (cur.capped && has)) {
      setSt({ ...cur, capped: true, shown: true, ratings: has ? value! : null });
    }
  }, [value, settled, iconsReady, cur]);
  return { ratings: cur.ratings, shown: cur.shown };
}
