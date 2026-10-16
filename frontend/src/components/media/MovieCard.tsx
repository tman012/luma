import { cn } from '@/utils/cn';
import type { HeroMovie } from '@/services/types';
import { useUiDensity } from '@/hooks/useUiDensity';

interface MovieCardProps {
  movie: HeroMovie;
  className?: string;
  onClick?: () => void;
}

export function MovieCard({ movie, className, onClick }: MovieCardProps) {
  const d = useUiDensity();

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'group shrink-0 cursor-pointer text-left transition-transform duration-300 hover:scale-105',
        d.isTablet ? 'w-[160px]' : 'w-[200px]',
        className,
      )}
    >
      <div className="relative aspect-[2/3] overflow-hidden rounded-xl">
        {movie.posterUrl ? (
          <img
            src={movie.posterUrl}
            alt={movie.title}
            loading="lazy"
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-glass-10 text-text-muted text-sm">
            No Poster
          </div>
        )}
        {/* Hover gradient overlay */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
      </div>
      <div className="mt-2 px-1">
        <p className="truncate text-[14px] font-medium text-text-primary">
          {movie.title}
        </p>
        {movie.year && (
          <p className="text-[12px] text-text-muted">{movie.year}</p>
        )}
      </div>
    </button>
  );
}
