import { useEffect, useState } from 'react';

/** True while the media query matches (false where matchMedia is missing, e.g. some test DOMs). */
export function useMediaQuery(q: string): boolean {
  const [on, setOn] = useState(() => (typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(q).matches));
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const m = window.matchMedia(q);
    const sync = () => setOn(m.matches);
    sync();
    m.addEventListener?.('change', sync);
    return () => m.removeEventListener?.('change', sync);
  }, [q]);
  return on;
}

/** v175 (Designer): phone card layout below 600px. */
export const PHONE_CARD_QUERY = '(max-width: 599px)';
