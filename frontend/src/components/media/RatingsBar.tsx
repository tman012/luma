import type { Ratings } from '@/utils/modalParsers';
import { buildRatingItems, RATING_ICON_ASPECT } from '@/utils/ratingIcons';

type RatingsBarSize = 'sm' | 'md' | 'lg' | 'xl';

const sizePresets = {
  sm: { iconHeight: 20, fontSize: 14, gap: 24, itemGap: 5 },
  md: { iconHeight: 24, fontSize: 17, gap: 29, itemGap: 7 },
  lg: { iconHeight: 36, fontSize: 26, gap: 36, itemGap: 10 },
  xl: { iconHeight: 44, fontSize: 32, gap: 42, itemGap: 12 },
} as const;

interface RatingsBarProps {
  ratings: Ratings;
  size?: RatingsBarSize;
  style?: React.CSSProperties;
}

export function RatingsBar({ ratings, size = 'md', style }: RatingsBarProps) {
  const items = buildRatingItems(ratings);
  if (items.length === 0) return null;

  const preset = sizePresets[size];

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: `${preset.gap}px`,
        minHeight: `${preset.iconHeight}px`,
        marginTop: size === 'sm' ? '12px' : '14px',
        flexWrap: 'wrap',
        ...style,
      }}
    >
      {items.map((item) => {
        // Fixed box per icon (width from the file's aspect), set before load.
        const h = Math.round(preset.iconHeight * (item.heightScale ?? 1));
        const w = Math.round(h * (RATING_ICON_ASPECT[item.icon] ?? 1));
        return (
        <div
          key={item.label}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: `${preset.itemGap}px`,
            opacity: 0.9,
          }}
        >
          <img
            src={item.icon}
            alt={item.label}
            width={w}
            height={h}
            style={{ height: `${h}px`, width: `${w}px`, display: 'block', flexShrink: 0 }}
          />
          <span
            style={{
              fontSize: `${preset.fontSize}px`,
              // sm/lg (the sheets): the value line is exactly the icon height, so the bar is RATINGS_SLOT_SM / _LG tall
              // (the reserved row).
              ...(size === 'lg' || size === 'sm' ? { lineHeight: `${preset.iconHeight}px` } : {}),
              fontWeight: 600,
              color: '#fff',
            }}
          >
            {item.value}
          </span>
        </div>
        );
      })}
    </div>
  );
}

/** Height the sm bar takes (margin + icon row), reserved on the phone sheet
 *  while ratings are still loading so their arrival doesn't push content. */
export const RATINGS_SLOT_SM = 12 + sizePresets.sm.iconHeight;
/** Same for the lg bar (desktop sheets). */
export const RATINGS_SLOT_LG = 14 + sizePresets.lg.iconHeight;
