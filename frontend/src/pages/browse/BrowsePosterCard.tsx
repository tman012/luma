/**
 * BrowsePosterCard — poster grid card for library browse.
 *
 * Renders a clickable poster with title + year below.
 * Poster URLs from the browse endpoint are already proxy URLs
 * (/api/img/plex?url=...) — use as-is.
 */

import { useState, memo } from 'react';
import { cn } from '@/utils/cn';

export interface BrowsePosterCardProps {
  title: string;
  year: number | null;
  posterUrl: string | null;
  onClick: () => void;
}

export const BrowsePosterCard = memo(function BrowsePosterCard({
  title,
  year,
  posterUrl,
  onClick,
}: BrowsePosterCardProps) {
  const [imgLoaded, setImgLoaded] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative w-full text-left outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-xl"
    >
      <div className="relative aspect-[2/3] overflow-hidden rounded-xl">
        {posterUrl ? (
          <img
            src={posterUrl}
            alt={title}
            loading="lazy"
            draggable={false}
            className={cn(
              'h-full w-full object-cover transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-glass-10 text-sm text-text-muted">
            No Poster
          </div>
        )}

        {/* Hover gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
      </div>

      <div className="mt-2 px-0.5">
        <p className="truncate text-[14px] font-medium text-text-primary">{title}</p>
        {year && (
          <p className="mt-0.5 truncate text-[12px] text-text-muted">{year}</p>
        )}
      </div>
    </button>
  );
});
