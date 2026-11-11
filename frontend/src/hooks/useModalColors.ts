/** Color blur for the detail popup, settled before the backdrop shows: the
 * popup keeps its loading state until the palette is ready or COLOR_WAIT_MS
 * passes, so the blur fades in with the backdrop and never pops in after. A
 * palette that arrives after the wait is dropped for this opening.
 *
 * `colors` keeps the desktop popup's behavior (the tapped poster's seed first, the fetched palette replacing it).
 * `fetched` is the fetched palette alone, null until it has answered: the phone sheets draw only this one, so
 * their blur is the neutral color and then the final palette once, never the poster seed. */
import { useEffect, useState } from 'react';
import { modalColors, getSeedColors, peekModalColors, type ColorKind } from '@/services/modalColors';
import { remainingCap } from '@/services/modalPrefetch';
import type { UltraBlurColors } from '@/utils/modalParsers';

export const COLOR_WAIT_MS = 600;

export function useModalColors(
  kind: ColorKind, ratingKey: number | undefined, tmdbId: number | undefined, enabled = true,
): { colors: UltraBlurColors | null; settled: boolean; fetched: UltraBlurColors | null } {
  // A poster-derived seed (set by the tap that opened the popup) paints first;
  // the fetched palette then replaces it (the overlay crossfades), any time.
  const seedNow = getSeedColors(kind, ratingKey, tmdbId);
  const [colors, setColors] = useState<UltraBlurColors | null>(seedNow);
  const [settled, setSettled] = useState(!!seedNow);
  const [fetched, setFetched] = useState<UltraBlurColors | null>(() => (enabled ? peekModalColors(kind, ratingKey, tmdbId) ?? null : null));

  useEffect(() => {
    const seed = getSeedColors(kind, ratingKey, tmdbId);
    setColors(seed);
    setFetched(enabled ? peekModalColors(kind, ratingKey, tmdbId) ?? null : null);
    if (!enabled) { setSettled(!!seed); return; }
    if (!ratingKey && !tmdbId) { setSettled(true); return; }
    setSettled(!!seed);
    let live = true;
    let current = true;
    let waiting = !seed;
    const t = window.setTimeout(() => { if (live && waiting) { waiting = false; if (!seed) live = false; setSettled(true); } }, remainingCap(COLOR_WAIT_MS));
    modalColors(kind, ratingKey, tmdbId).then(c => {
      if (!current) return;
      // The phone sheet's palette still lands after the popup's wait: it only ever fades in, once.
      setFetched(c ?? null);
      if (!live) return;
      live = false;
      window.clearTimeout(t);
      if (c) setColors(c);
      setSettled(true);
    });
    return () => { live = false; current = false; window.clearTimeout(t); };
  }, [kind, ratingKey, tmdbId, enabled]);

  return { colors, settled, fetched };
}
