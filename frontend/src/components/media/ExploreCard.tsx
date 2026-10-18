import { cn } from '@/utils/cn';
import { formatSize } from '@/utils/image';
import type { ExploreMovie } from '@/services/types';

interface ExploreCardProps {
  movie: ExploreMovie;
  className?: string;
  onClick?: () => void;
}

export function ExploreCard({ movie, className, onClick }: ExploreCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'group glass-panel cursor-pointer overflow-hidden rounded-2xl text-left transition-transform duration-300 hover:scale-[1.02]',
        className,
      )}
    >
      {/* Poster */}
      <div className="relative aspect-[2/3] overflow-hidden">
        {movie.posterUrl ? (
          <img
            src={movie.posterUrl}
            alt={movie.title}
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-glass-10 text-text-muted text-sm">
            No Poster
          </div>
        )}

        {/* Resolution badge */}
        {movie.resolution && (
          <span className="absolute right-2 top-2 rounded-full bg-glass-20 px-2.5 py-0.5 text-[12px] font-semibold text-text-primary backdrop-blur-sm">
            {movie.resolution}
          </span>
        )}

        {/* Bottom gradient */}
        <div className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-black/80 to-transparent" />
      </div>

      {/* Info */}
      <div className="p-3">
        <p className="truncate text-[14px] font-medium text-text-primary">
          {movie.title}
        </p>
        <div className="mt-1.5 flex items-center gap-3 text-[12px] text-text-muted">
          {movie.year && <span>{movie.year}</span>}
          {movie.seeders > 0 && (
            <span className="flex items-center gap-1">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-seeder-green" />
              {movie.seeders}
            </span>
          )}
          {movie.sizeBytes > 0 && (
            <span>{formatSize(movie.sizeBytes)}</span>
          )}
        </div>
        {movie.tracker && (
          <span className="mt-1.5 inline-block rounded bg-glass-10 px-2 py-0.5 text-[12px] text-text-faint">
            {movie.tracker}
          </span>
        )}
      </div>
    </button>
  );
}
