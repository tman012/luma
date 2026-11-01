/** Movie/TV sheet ratings as ONE settled row: the badges appear together, once.
 *
 * - `value` is the merged ratings so far; `settled` is true once every source for this open has answered
 *   (a failed or empty source counts as answered).
 * - One deadline (revealGate, SURFACE_DEADLINE_MS) counted from the tap that opened the sheet, or from when the
 *   details landed (`active`) for an open without a tap. The row latches once the sources have settled and the badge
 *   icons have decoded, or at the deadline, whichever comes first. At the deadline whatever is ready shows, together;
 *   with nothing in hand it does not lock empty: the first non-empty answer reveals (one fade), or the empty row once
 *   every source has answered.
 * - The icons ship in the app bundle and are decoded at boot, so they are normally ready with the data; past the
 *   deadline a stuck icon never holds the row.
 * - Once latched the row never changes for that open (`openKey`): a source that answers later is dropped. */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Ratings } from '@/utils/modalParsers';
import { buildRatingItems, decodeRatingIcon, ratingIconDecoded } from '@/utils/ratingIcons';
import { deadlineLeft } from '@/loading/revealGate';
import { tapElapsed } from '@/services/modalPrefetch';


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
interface St { key: unknown; capped: boolean; shown: boolean; ratings: Ratings | null; startedAt?: number; hadAtCap?: boolean }

export function useRatingsSettle(value: Ratings | null | undefined, settled: boolean, openKey: unknown, active = true): RatingsSettleState {
  const [st, setSt] = useState<St>({ key: openKey, capped: false, shown: false, ratings: null });
  const cur: St = st.key === openKey ? st : { key: openKey, capped: false, shown: false, ratings: null };
  const icons = useMemo(() => (value ? [...new Set(buildRatingItems(value).map(i => i.icon))] : []), [value]);
  const iconsReady = useIconsReady(icons);
  const hasRef = useRef(false);
  useEffect(() => {
    setSt(s => (s.key === openKey ? s : { key: openKey, capped: false, shown: false, ratings: null }));
  }, [openKey]);
  // The one deadline: from the tap when there was one, else from when the details landed; once per open.
  useEffect(() => {
    if (!active) return;
    const tap = tapElapsed();
    const startedAt = cur.startedAt ?? Date.now() - (tap ?? 0);
    if (cur.startedAt === undefined) setSt(s => (s.key === openKey && s.startedAt === undefined ? { ...s, startedAt } : s));
    const t = window.setTimeout(() => setSt(s => (s.key === openKey && !s.capped ? { ...s, capped: true, hadAtCap: hasRef.current } : s)), deadlineLeft(startedAt));
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openKey, active]);
  const has = !!value && buildRatingItems(value).length > 0;
  hasRef.current = has;
  // Settled: lock with whatever is there, empty included, once the icons are ready (or the deadline passed).
  // At the deadline: lock with what is in hand. With nothing in hand then, wait for every source (v341, PM): the
  // late badges arrive as ONE group into the reserved slot, never one source at a time.
  const ready = !cur.shown && ((settled && (iconsReady || cur.capped)) || (cur.capped && has && cur.hadAtCap !== false));
  useEffect(() => {
    if (ready) setSt({ ...cur, capped: true, shown: true, ratings: has ? value! : null });
  }, [ready, value, has, cur]);
  return { ratings: cur.ratings, shown: cur.shown };
}
