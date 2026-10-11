import { cn } from '@/utils/cn';
import { ExploreCard } from './ExploreCard';
import type { ExploreMovie } from '@/services/types';
import { useUiDensity } from '@/hooks/useUiDensity';
import { uiDensityTokens } from '@/ui/uiDensityTokens';

interface ExploreGridProps {
  movies: ExploreMovie[];
  className?: string;
}

export function ExploreGrid({ movies, className }: ExploreGridProps) {
  const d = useUiDensity();
  const tk = uiDensityTokens(d);

  if (movies.length === 0) {
    return (
      <div className="flex items-center justify-center py-20 text-text-muted">
        No explore content available
      </div>
    );
  }

  return (
    <div
      className={cn(
        'grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] px-[60px]',
        tk.spacing.railGap,
        className,
      )}
    >
      {movies.map((movie) => (
        <ExploreCard key={`${movie.tmdbId}-${movie.tracker}`} movie={movie} />
      ))}
    </div>
  );
}
