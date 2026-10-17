/** Phone: warm a card's detail popup while it's on (or near) the screen, at
 * most WARM_CONCURRENCY at a time; a card that scrolls away before its turn
 * is dropped (same scheme as the :5002 home rails). */
import { useEffect } from 'react';
import { scheduleWarm } from '@/services/modalPrefetch';

export function useWarmWhenVisible(
  ref: React.RefObject<HTMLElement | null>,
  key: string | null,
  run: () => Promise<void>,
): void {
  useEffect(() => {
    const el = ref.current;
    if (!key || !el || typeof IntersectionObserver === 'undefined') return;
    let cancel = () => {};
    const io = new IntersectionObserver((entries) => {
      const e = entries[entries.length - 1];
      cancel();
      cancel = e?.isIntersecting ? scheduleWarm(key, run) : () => {};
    }, { rootMargin: '150px' });
    io.observe(el);
    return () => { io.disconnect(); cancel(); };
    // run is derived from key; re-observing on every render is not needed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref, key]);
}
