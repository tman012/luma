import { memo } from 'react';
import { cn } from '@/utils/cn';
import { RATING_ICONS } from '@/utils/ratingIcons';

const sizeMap = {
  sm: 'w-4 h-4',    // 16px — inline in review cards
  md: 'w-6 h-6',    // 24px
  lg: 'w-8 h-8',    // 32px
  xl: 'w-10 h-10',  // 40px — score slab hero badge
} as const;

/** HTML width/height to prevent layout shift before CSS loads */
const intrinsicSize = { sm: 16, md: 24, lg: 32, xl: 40 } as const;

interface TomatoBadgeProps {
  fresh: boolean;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  className?: string;
}

export const TomatoBadge = memo(function TomatoBadge({ fresh, size = 'md', className }: TomatoBadgeProps) {
  const src = fresh ? RATING_ICONS.rtCriticFresh : RATING_ICONS.rtCriticRotten;
  const px = intrinsicSize[size];

  return (
    <img
      src={src}
      alt={fresh ? 'Fresh' : 'Rotten'}
      width={px}
      height={px}
      className={cn(sizeMap[size], 'flex-shrink-0 object-contain', className)}
      draggable={false}
    />
  );
});
