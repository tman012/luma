/** Color blur for the detail popup, settled before the backdrop shows: the
 * popup keeps its loading state until the palette is ready or COLOR_WAIT_MS
 * passes, so the blur fades in with the backdrop and never pops in after. A
 * palette that arrives after the wait is dropped for this opening. */
import { useEffect, useState } from 'react';
import { modalColors, type ColorKind } from '@/services/modalColors';
import type { UltraBlurColors } from '@/utils/modalParsers';

export const COLOR_WAIT_MS = 1800;

export function useModalColors(
  kind: ColorKind, ratingKey: number | undefined, tmdbId: number | undefined, enabled = true,
): { colors: UltraBlurColors | null; settled: boolean } {
  const [colors, setColors] = useState<UltraBlurColors | null>(null);
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    setColors(null);
    if (!enabled) { setSettled(false); return; }
    if (!ratingKey && !tmdbId) { setSettled(true); return; }
    setSettled(false);
    let live = true;
    const t = window.setTimeout(() => { if (live) { live = false; setSettled(true); } }, COLOR_WAIT_MS);
    modalColors(kind, ratingKey, tmdbId).then(c => {
      if (!live) return;
      live = false;
      window.clearTimeout(t);
      setColors(c);
      setSettled(true);
    });
    return () => { live = false; window.clearTimeout(t); };
  }, [kind, ratingKey, tmdbId, enabled]);

  return { colors, settled };
}
