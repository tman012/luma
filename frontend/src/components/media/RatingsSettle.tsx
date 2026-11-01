/** The sheet's ratings row: a reserved box that fades the settled badges in once (useRatingsSettle), with the
 *  content-settle fade (MOTION.dur.contentFade, MOTION.ease.contentFade). The fade starts once the row's own icon
 *  elements have decoded (bundled and decoded at boot, so normally at once; at most RATINGS_ELEMENT_DECODE_MS), so
 *  text and icons first paint in the same frame. The row's 0.9 dim is on this box, not on each badge, so text and
 *  icons share one opacity. */
import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import type { Ratings } from '@/utils/modalParsers';
import { MOTION, reducedMotion } from '@/theme/motion';
import { RatingsBar } from '@/components/media/RatingsBar';
import type { RatingsSettleState } from '@/hooks/useRatingsSettle';

/** The last wait for the row's own <img> elements, past the shared deadline (decoded icons resolve at once). */
export const RATINGS_ELEMENT_DECODE_MS = 150;
const SLOT_OPACITY = 0.9;

export function RatingsSettle({ settle, size, reserve, style, barStyle }: {
  settle: RatingsSettleState; size: 'sm' | 'lg'; reserve: number; style?: CSSProperties; barStyle?: CSSProperties;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [painted, setPainted] = useState<Ratings | null | undefined>(undefined);
  const visible = settle.shown && painted === settle.ratings;
  useLayoutEffect(() => {
    if (!settle.shown) return;
    const imgs = Array.from(box.current?.querySelectorAll('img') ?? []);
    const decodes = imgs.filter(i => typeof i.decode === 'function');
    if (!decodes.length) { setPainted(settle.ratings); return; }
    let live = true;
    const done = () => { if (live) setPainted(settle.ratings); };
    const t = window.setTimeout(done, RATINGS_ELEMENT_DECODE_MS);
    Promise.all(decodes.map(i => i.decode().catch(() => {}))).then(done);
    return () => { live = false; window.clearTimeout(t); };
  }, [settle.shown, settle.ratings]);
  return (
    <div ref={box} data-ratings-slot="" data-ratings={visible ? 'shown' : 'hold'}
      // flow-root: the bar's top margin stays inside the slot (no margin collapse), so the box is the same before and
      // after the reveal. A caller's display (desktop: flex) wins.
      style={{ display: 'flow-root', minHeight: reserve ? `${reserve}px` : 0, ...style,
        opacity: visible ? SLOT_OPACITY : 0,
        transition: reducedMotion() ? 'none' : `opacity ${MOTION.dur.contentFade}ms ${MOTION.ease.contentFade}` }}>
      {settle.ratings && <RatingsBar ratings={settle.ratings} size={size} style={barStyle} dim={false} />}
    </div>
  );
}
