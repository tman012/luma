/** Color blur for the detail popup, settled before the backdrop shows: the
 * popup keeps its loading state until the palette is ready or COLOR_WAIT_MS
 * passes, so the blur fades in with the backdrop and never pops in after. A
 * palette that arrives after the wait is dropped for this opening. */
import { useEffect, useState } from 'react';
import { modalColors, getSeedColors, type ColorKind } from '@/services/modalColors';
import { remainingCap } from '@/services/modalPrefetch';
import type { UltraBlurColors } from '@/utils/modalParsers';

export const COLOR_WAIT_MS = 600;

export function useModalColors(
  kind: ColorKind, ratingKey: number | undefined, tmdbId: number | undefined, enabled = true,
): { colors: UltraBlurColors | null; settled: boolean } {
  // A poster-derived seed (set by the tap that opened the popup) paints first;
  // the fetched palette then replaces it (the overlay crossfades), any time.
  const seedNow = getSeedColors(kind, ratingKey, tmdbId);
  const [colors, setColors] = useState<UltraBlurColors | null>(seedNow);
  const [settled, setSettled] = useState(!!seedNow);

  useEffect(() => {
    const seed = getSeedColors(kind, ratingKey, tmdbId);
    setColors(seed);
    if (!enabled) { setSettled(!!seed); return; }
    if (!ratingKey && !tmdbId) { setSettled(true); return; }
    setSettled(!!seed);
    let live = true;
    let waiting = !seed;
    const t = window.setTimeout(() => { if (live && waiting) { waiting = false; if (!seed) live = false; setSettled(true); } }, remainingCap(COLOR_WAIT_MS));
    modalColors(kind, ratingKey, tmdbId).then(c => {
      if (!live) return;
      live = false;
      window.clearTimeout(t);
      if (c) setColors(c);
      setSettled(true);
    });
    return () => { live = false; window.clearTimeout(t); };
  }, [kind, ratingKey, tmdbId, enabled]);

  return { colors, settled };
}
