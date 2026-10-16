import { memo, useCallback } from 'react';
import { cn } from '@/utils/cn';
import type { ReviewTab, CriticReview, AudienceReview } from '@/types/reviews';
import { TomatoBadge } from './TomatoBadge';

interface ReviewCardProps {
  review: CriticReview | AudienceReview;
  activeTab: ReviewTab;
  index: number;
  cardKey: string;
  expanded: boolean;
  onToggleExpand: (cardKey: string) => void;
}

/** Max items that receive staggered entrance animation */
const MAX_STAGGER = 10;

function StarRating({ rating }: { rating: number }) {
  const full = Math.round(rating);
  return (
    <span className="flex items-center gap-0.5 text-amber-400" aria-label={`${rating} out of 5 stars`}>
      {Array.from({ length: 5 }, (_, i) => (
        <svg
          key={i}
          viewBox="0 0 20 20"
          fill={i < full ? 'currentColor' : 'none'}
          stroke="currentColor"
          strokeWidth={1.5}
          className={cn('h-3.5 w-3.5', i >= full && 'text-white/20')}
        >
          <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
        </svg>
      ))}
      <span className="ml-1 text-xs font-semibold tabular-nums text-white/60">
        {rating}/5
      </span>
    </span>
  );
}

export const ReviewCard = memo(function ReviewCard({
  review,
  activeTab,
  index,
  cardKey,
  expanded,
  onToggleExpand,
}: ReviewCardProps) {
  const isCritic = activeTab === 'critics';
  const criticReview = isCritic ? (review as CriticReview) : null;
  const audienceReview = !isCritic ? (review as AudienceReview) : null;

  const handleToggle = useCallback(() => {
    onToggleExpand(cardKey);
  }, [onToggleExpand, cardKey]);

  // Quote may be long enough to warrant expand/collapse
  const quote = review.quote ?? '';
  const isLongQuote = quote.length > 200;

  // Stagger delay for entrance animation (capped at MAX_STAGGER items)
  const staggerDelay = index < MAX_STAGGER ? index * 40 : 0;

  return (
    <div
      className={cn(
        'glass-panel rounded-2xl border border-white/[0.08] p-4 md:p-5',
        'motion-safe:animate-[reviewCardEnter_300ms_ease-out_both]',
        'lg:transition-transform lg:duration-200 lg:ease-out',
        'lg:motion-safe:hover:scale-[1.01] lg:motion-safe:hover:shadow-lg',
        'active:bg-white/[0.12]'
      )}
      style={{ animationDelay: `${staggerDelay}ms` }}
    >
      {/* Quote */}
      <p
        className={cn(
          'text-[13.5px] leading-[1.45] text-white/90 md:text-[15px]',
          !expanded && isLongQuote && 'line-clamp-3'
        )}
      >
        &ldquo;{quote}&rdquo;
      </p>

      {/* Expand toggle for long quotes */}
      {isLongQuote && (
        <button
          type="button"
          onClick={handleToggle}
          className={cn(
            'mt-2 min-h-[44px] min-w-[44px] px-2 text-[13px] font-medium',
            'text-accent hover:text-accent-hover active:text-accent-hover active:opacity-80',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/20 focus-visible:rounded-md'
          )}
        >
          {expanded ? 'Show less' : 'Read more'}
        </button>
      )}

      {/* Meta row */}
      <div className="mt-3 flex items-center justify-between gap-3">
        {/* Left: reviewer info */}
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {/* Super Reviewer badge */}
          {audienceReview?.is_super_reviewer && (
            <span className="shrink-0 rounded-md border border-amber-400/30 bg-amber-400/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-400">
              Super Reviewer
            </span>
          )}

          {/* Name and publication */}
          <div className="min-w-0 truncate">
            <span className="text-sm font-semibold text-white/80">
              {isCritic ? criticReview!.critic : audienceReview!.user}
            </span>
            {criticReview?.publication && (
              <span className="text-xs text-white/50">
                {' '}&mdash; {criticReview.publication}
              </span>
            )}
          </div>
        </div>

        {/* Right: rating chip */}
        <div className="flex shrink-0 items-center gap-1.5">
          {isCritic && criticReview && (
            <>
              <TomatoBadge fresh={criticReview.fresh} size="sm" />
              {criticReview.score && (
                <span className="text-xs font-semibold tabular-nums text-white/60">
                  {criticReview.score}
                </span>
              )}
            </>
          )}
          {!isCritic && audienceReview?.rating != null && (
            <StarRating rating={audienceReview.rating} />
          )}
        </div>
      </div>
    </div>
  );
});
