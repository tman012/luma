/**
 * MobileDescribeResults — vertical scrollable card list for Describe It results on mobile.
 *
 * Replaces the full-bleed backdrop carousel (DescribeResultsCarousel) which is designed
 * for large screens. Shows poster + title + AI category + AI reason in compact cards.
 */

import { cn } from '@/utils/cn';
import { categoryColor } from './DescribeMode';
import type { Recommendation } from './DescribeMode';
import type { VibeItem } from '@/types/vibe';

interface Props {
  recommendations: Recommendation[];
  onClose: () => void;
  onOpenDetail: (item: VibeItem) => void;
  onCategorySearch?: (category: string) => void;
}

export function MobileDescribeResults({ recommendations, onClose: _onClose, onOpenDetail, onCategorySearch }: Props) {
  return (
    <div
      className="flex flex-1 flex-col bg-kiosk-black"
      style={{ animation: 'mobilePageEnter 0.25s ease-out' }}
    >
      {/* Result count */}
      <div className="shrink-0 px-4 pt-3 pb-1">
        <span className="text-[13px] text-white/30">
          {recommendations.length} result{recommendations.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Card list */}
      <div
        className="px-4 pb-8 pt-1"
        style={{ paddingBottom: 'max(32px, env(safe-area-inset-bottom))' }}
      >
        {recommendations.map((rec, idx) => {
          const colors = categoryColor(rec.aiCategory);
          return (
            <button
              key={`${rec.vibeItem.tmdbId ?? rec.vibeItem.ratingKey}-${idx}`}
              type="button"
              onClick={() => onOpenDetail(rec.vibeItem)}
              className={cn(
                'mb-3 flex w-full gap-3.5 rounded-xl border border-white/6 bg-white/4 p-3 text-left',
                'transition-all duration-200 active:scale-[0.98] active:bg-white/8',
              )}
              style={{
                opacity: 0,
                animation: `mobileCardEnter 0.5s cubic-bezier(0.22, 1, 0.36, 1) ${80 + idx * 60}ms forwards`,
              }}
            >
              {/* Poster */}
              <div className="h-[120px] w-[80px] flex-shrink-0 overflow-hidden rounded-lg bg-white/5">
                {rec.vibeItem.posterUrl ? (
                  <img
                    src={rec.vibeItem.posterUrl}
                    alt=""
                    className="h-full w-full object-cover"
                    loading={idx < 6 ? 'eager' : 'lazy'}
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center text-[24px] text-white/15">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M18 4l2 4h-3l-2-4h-2l2 4h-3l-2-4H8l2 4H7L5 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V4h-4z"/></svg>
                  </div>
                )}
              </div>

              {/* Content */}
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                {/* Title row */}
                <div className="flex items-baseline gap-2">
                  <span className="truncate text-[15px] font-semibold text-white">
                    {rec.vibeItem.title}
                  </span>
                  {rec.vibeItem.year && (
                    <span className="flex-shrink-0 text-[13px] text-white/30">
                      {rec.vibeItem.year}
                    </span>
                  )}
                </div>

                {/* Type + AI category */}
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[12px] capitalize text-white/30">
                    {rec.vibeItem.type === 'tv' ? 'TV show' : 'Movie'}
                  </span>
                  {rec.aiCategory && (
                    <span
                      role={onCategorySearch ? 'button' : undefined}
                      onClick={onCategorySearch ? (e) => {
                        e.stopPropagation();
                        onCategorySearch(rec.aiCategory);
                      } : undefined}
                      className={cn(
                        'rounded-full border px-2 py-0.5 text-[12px] font-medium',
                        colors.bg, colors.text, colors.border,
                        onCategorySearch && 'active:scale-95 transition-transform cursor-pointer',
                      )}
                    >
                      {rec.aiCategory}
                    </span>
                  )}
                </div>

                {/* AI reason */}
                {rec.aiReason && (
                  <p className="mt-0.5 text-[13px] leading-[1.4] text-white/45">
                    {rec.aiReason}
                  </p>
                )}

                {/* Ownership badge */}
                {rec.owned && (
                  <span className="mt-1 inline-flex w-fit items-center gap-1 rounded-full bg-emerald-500/12 px-2 py-0.5 text-[12px] font-medium text-emerald-400">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    In Library
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
