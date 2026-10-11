import type { Ratings } from '@/utils/modalParsers';
import { buildRatingItems } from '@/utils/ratingIcons';

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
        marginTop: size === 'sm' ? '12px' : '14px',
        flexWrap: 'wrap',
        ...style,
      }}
    >
      {items.map((item) => (
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
            style={{ height: `${Math.round(preset.iconHeight * (item.heightScale ?? 1))}px`, width: 'auto' }}
          />
          <span
            style={{
              fontSize: `${preset.fontSize}px`,
              fontWeight: 600,
              color: '#fff',
            }}
          >
            {item.value}
          </span>
        </div>
      ))}
    </div>
  );
}
