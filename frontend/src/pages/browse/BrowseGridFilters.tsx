/**
 * BrowseGridFilters — compact sort + genre filter pills for the browse grid.
 */

import { useState, useEffect, useRef } from 'react';
import { cn } from '@/utils/cn';

// ---------------------------------------------------------------------------
// Sort types
// ---------------------------------------------------------------------------

export type SortKey = 'added_date' | 'title' | 'year' | 'rating';
export type SortDirection = 'asc' | 'desc';

export interface SortState {
  key: SortKey;
  direction: SortDirection;
}

const SORT_OPTIONS: { value: SortKey; label: string; defaultDir: SortDirection }[] = [
  { value: 'added_date', label: 'Recent', defaultDir: 'desc' },
  { value: 'title', label: 'Title', defaultDir: 'asc' },
  { value: 'year', label: 'Year', defaultDir: 'desc' },
  { value: 'rating', label: 'Rating', defaultDir: 'desc' },
];

// ---------------------------------------------------------------------------
// Pill dropdown
// ---------------------------------------------------------------------------

function PillDropdown({
  value,
  options,
  onChange,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const selectedLabel = options.find((o) => o.value === value)?.label ?? value;

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('pointerdown', handleClick);
    return () => document.removeEventListener('pointerdown', handleClick);
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        className={cn(
          'flex items-center gap-1.5 rounded-full px-3.5 py-[7px] text-[13px] font-medium transition-colors',
          open
            ? 'bg-white/15 text-white'
            : 'bg-white/8 text-white/70 active:bg-white/12',
        )}
      >
        <span>{selectedLabel}</span>
        <svg
          width="12" height="12" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
          className={cn('transition-transform', open && 'rotate-180')}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-1.5 min-w-[140px] overflow-hidden rounded-2xl border border-white/10 bg-kiosk-black/95 py-1 shadow-2xl backdrop-blur-xl">
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => { onChange(o.value); setOpen(false); }}
              className={cn(
                'flex w-full items-center px-4 py-2.5 text-left text-[13px] font-medium transition-colors',
                o.value === value
                  ? 'text-white bg-white/10'
                  : 'text-white/60 hover:bg-white/5 hover:text-white/80',
              )}
            >
              {o.label}
              {o.value === value && (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="ml-auto">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BrowseGridFilters
// ---------------------------------------------------------------------------

interface BrowseGridFiltersProps {
  sort: SortState;
  onSortChange: (sort: SortState) => void;
  genre: string;
  onGenreChange: (genre: string) => void;
  genres: string[];
  totalCount: number;
  loadedCount: number;
}

export function BrowseGridFilters({
  sort,
  onSortChange,
  genre,
  onGenreChange,
  genres,
}: BrowseGridFiltersProps) {
  const handleSortChange = (value: string) => {
    const key = value as SortKey;
    if (key === sort.key) {
      onSortChange({ key, direction: sort.direction === 'asc' ? 'desc' : 'asc' });
    } else {
      const opt = SORT_OPTIONS.find((o) => o.value === key);
      onSortChange({ key, direction: opt?.defaultDir ?? 'desc' });
    }
  };

  const genreOptions = [
    { value: '', label: 'All Genres' },
    ...genres.map((g) => ({ value: g, label: g })),
  ];

  return (
    <div className="flex items-center gap-2">
      <PillDropdown
        value={sort.key}
        options={SORT_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
        onChange={handleSortChange}
      />

      {genres.length > 0 && (
        <PillDropdown
          value={genre}
          options={genreOptions}
          onChange={onGenreChange}
        />
      )}
    </div>
  );
}
