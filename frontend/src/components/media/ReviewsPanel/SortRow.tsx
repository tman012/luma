import { memo } from 'react';
import { cn } from '@/utils/cn';
import type { ReviewTab, SortMode } from '@/types/reviews';

interface SortRowProps {
  sortMode: SortMode;
  onSortChange: (mode: SortMode) => void;
  activeTab: ReviewTab;
  hasDateData: boolean;
  /** Larger pills for two-column layouts */
  large?: boolean;
}

const SORT_OPTIONS: { mode: SortMode; label: string }[] = [
  { mode: 'top', label: 'Top' },
  { mode: 'newest', label: 'Newest' },
  { mode: 'positive', label: 'Positive' },
  { mode: 'critical', label: 'Critical' },
];

export const SortRow = memo(function SortRow({ sortMode, onSortChange, activeTab: _activeTab, hasDateData, large }: SortRowProps) {
  return (
    <div
      className={cn('flex overflow-x-auto overscroll-contain pb-1', large ? 'gap-3' : 'gap-2')}
      role="group"
      aria-label="Sort reviews"
      style={{ touchAction: 'pan-x', WebkitOverflowScrolling: 'touch' }}
    >
      {SORT_OPTIONS.map(({ mode, label }) => {
        const isActive = sortMode === mode;
        const isDisabled = mode === 'newest' && !hasDateData;

        return (
          <button
            key={mode}
            type="button"
            onClick={() => {
              if (!isDisabled) onSortChange(mode);
            }}
            disabled={isDisabled}
            aria-pressed={isActive}
            className={cn(
              'shrink-0 rounded-full font-medium transition-all duration-150',
              'active:scale-95',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/25',
              large
                ? 'min-h-[52px] px-7 text-[17px]'
                : 'min-h-[44px] px-4 text-sm',
              isActive
                ? 'glass-panel-strong border border-accent/30 text-white'
                : isDisabled
                  ? 'glass-panel-subtle border border-transparent text-white/25 cursor-not-allowed'
                  : 'glass-panel-subtle border border-transparent text-white/60 hover:text-white/80 hover:border-white/10'
            )}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
});
