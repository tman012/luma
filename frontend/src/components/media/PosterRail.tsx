import { useRef, useCallback } from 'react';
import { cn } from '@/utils/cn';
import { MovieCard } from './MovieCard';
import type { HeroMovie } from '@/services/types';
import { useUiDensity } from '@/hooks/useUiDensity';
import { uiDensityTokens } from '@/ui/uiDensityTokens';

interface PosterRailProps {
  title: string;
  movies: HeroMovie[];
  className?: string;
}

export function PosterRail({ title, movies, className }: PosterRailProps) {
  const d = useUiDensity();
  const tk = uiDensityTokens(d);
  const scrollRef = useRef<HTMLDivElement>(null);

  const scroll = useCallback((direction: 'left' | 'right') => {
    if (!scrollRef.current) return;
    const amount = direction === 'left' ? -600 : 600;
    scrollRef.current.scrollBy({ left: amount, behavior: 'smooth' });
  }, []);

  if (movies.length === 0) return null;

  return (
    <section className={cn('relative', className)}>
      {/* Header */}
      <div className="mb-4 flex items-center justify-between px-[60px]">
        <h2 className={`${tk.typography.railTitle} font-semibold text-text-primary`}>{title}</h2>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => scroll('left')}
            className={cn('flex h-10 w-10 items-center justify-center rounded-full bg-glass-10 text-text-secondary transition-colors hover:bg-glass-20', tk.targets.minBtn)}
            aria-label="Scroll left"
          >
            ‹
          </button>
          <button
            type="button"
            onClick={() => scroll('right')}
            className={cn('flex h-10 w-10 items-center justify-center rounded-full bg-glass-10 text-text-secondary transition-colors hover:bg-glass-20', tk.targets.minBtn)}
            aria-label="Scroll right"
          >
            ›
          </button>
        </div>
      </div>

      {/* Scrollable rail */}
      <div
        ref={scrollRef}
        className={cn('flex overflow-x-auto px-[60px] pb-4 snap-x snap-mandatory', tk.spacing.railGap)}
      >
        {movies.map((movie) => (
          <div key={movie.tmdbId} className="snap-start">
            <MovieCard movie={movie} />
          </div>
        ))}
      </div>
    </section>
  );
}
