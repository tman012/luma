/**
 * AlphabetSidebar — vertical letter quick-nav for title-sorted grids.
 *
 * Only visible when sort is set to 'title'. Each letter scrolls
 * the grid to the first item starting with that letter.
 */

import { useMemo, useCallback } from 'react';
import { cn } from '@/utils/cn';

const LETTERS = '#ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');

interface AlphabetSidebarProps {
  /** All titles currently in the grid (used to determine active letters). */
  titles: string[];
  /** Callback when a letter is tapped. */
  onLetterClick: (letter: string) => void;
}

export function AlphabetSidebar({ titles, onLetterClick }: AlphabetSidebarProps) {
  const activeLetters = useMemo(() => {
    const set = new Set<string>();
    for (const t of titles) {
      if (!t) continue;
      const first = t.charAt(0).toUpperCase();
      if (/[A-Z]/.test(first)) {
        set.add(first);
      } else {
        set.add('#');
      }
    }
    return set;
  }, [titles]);

  const handleClick = useCallback(
    (letter: string) => {
      onLetterClick(letter);
    },
    [onLetterClick],
  );

  return (
    <div className="fixed right-1.5 top-1/2 z-40 -translate-y-1/2 flex flex-col items-center gap-[2px] sm:right-3">
      {LETTERS.map((letter) => {
        const active = activeLetters.has(letter);
        return (
          <button
            key={letter}
            type="button"
            onClick={() => handleClick(letter)}
            disabled={!active}
            className={cn(
              'flex h-[22px] w-[22px] items-center justify-center rounded-full text-[11px] font-semibold transition-colors sm:h-6 sm:w-6 sm:text-[12px]',
              active
                ? 'text-text-primary hover:bg-white/10 active:bg-white/20'
                : 'text-white/15 cursor-default',
            )}
          >
            {letter}
          </button>
        );
      })}
    </div>
  );
}
