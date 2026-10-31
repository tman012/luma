/** The sheet's ratings row: a reserved box that fades the settled badges in once (useRatingsSettle), with the
 *  content-settle fade (MOTION.dur.contentFade, MOTION.ease.contentFade). */
import type { CSSProperties } from 'react';
import { MOTION, reducedMotion } from '@/theme/motion';
import { RatingsBar } from '@/components/media/RatingsBar';
import type { RatingsSettleState } from '@/hooks/useRatingsSettle';

export function RatingsSettle({ settle, size, reserve, style, barStyle }: {
  settle: RatingsSettleState; size: 'sm' | 'lg'; reserve: number; style?: CSSProperties; barStyle?: CSSProperties;
}) {
  return (
    <div data-ratings-slot="" data-ratings={settle.shown ? 'shown' : 'hold'}
      style={{ minHeight: reserve ? `${reserve}px` : 0, ...style,
        opacity: settle.shown ? 1 : 0,
        transition: reducedMotion() ? 'none' : `opacity ${MOTION.dur.contentFade}ms ${MOTION.ease.contentFade}` }}>
      {settle.ratings && <RatingsBar ratings={settle.ratings} size={size} style={barStyle} />}
    </div>
  );
}
