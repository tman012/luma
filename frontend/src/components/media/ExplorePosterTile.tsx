/**
 * ExplorePosterTile — portrait poster card for Explore scroll rails (web/tablet).
 *
 * 2:3 aspect ratio poster with:
 *   - Lazy loading + opacity fade-in
 *   - Status orb (top-left): green=owned, blue=watchlist, red=neither
 *   - IMDb rating badge (bottom-right) when not owned
 *   - Title + year below card
 *   - Hover: scale(1.03) + shadow lift (reuses .explore-tile CSS)
 *
 * Width controlled by `data-poster-tile` attr + CSS overrides per breakpoint.
 */

import { memo, useState } from 'react';
import type { ExploreTileModel } from '@/types/explore';
import { cn } from '@/utils/cn';
import { getOrbColor } from '@/utils/orbColor';
import { useUiDensity } from '@/hooks/useUiDensity';

export const ExplorePosterTile = memo(function ExplorePosterTile({
  tile,
  onClick,
}: {
  tile: ExploreTileModel;
  onClick: () => void;
}) {
  const d = useUiDensity();
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);
  const imageUrl = tile.posterUrl ?? tile.backdropUrl;
  const orb = getOrbColor({ isInPlex: tile.isInPlex, isInWatchlist: tile.isInWatchlist, ownerType: tile.ownerType, resolved: !!tile.ownershipKnown });

  return (
    <button
      type="button"
      onClick={onClick}
      data-poster-tile
      className={cn(
        `explore-tile group relative shrink-0 snap-start text-left`,
        d.isTablet ? 'w-[180px]' : 'w-[220px]',
        'outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-xl',
      )}
    >
      <div className="relative aspect-[2/3] overflow-hidden rounded-xl">
        {imageUrl && !imgError ? (
          <img
            src={imageUrl}
            alt={tile.title}
            loading="lazy"
            draggable={false}
            className={cn(
              'h-full w-full object-cover transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={() => setImgError(true)}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-glass-10 text-sm text-text-muted">
            No Poster
          </div>
        )}

        {/* Hover gradient */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />

        {/* Status orb (top-left) — hidden until ownership is resolved */}
        {orb && (
          <div className="absolute left-3 top-3">
            <span
              className={cn('block h-3 w-3 rounded-full', orb.bg)}
              style={{ boxShadow: orb.glow }}
            />
          </div>
        )}

        {/* IMDb rating badge (bottom-right) — only for unowned */}
        {tile.voteAverage != null && tile.voteAverage > 0 && !tile.isInPlex && (
          <div className="absolute bottom-2 right-2 flex items-center gap-1 rounded-md bg-black/70 px-2 py-0.5 backdrop-blur-sm">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="#fbbf24" stroke="none">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
            <span className="text-[12px] font-semibold text-white/90">
              {tile.voteAverage.toFixed(1)}
            </span>
          </div>
        )}
      </div>

      {/* Title + year */}
      <div className="mt-2 px-1">
        <p className="truncate text-[15px] font-medium text-text-primary">
          {tile.title}
        </p>
        {tile.year != null && (
          <p className="text-[13px] text-text-muted">{tile.year}</p>
        )}
      </div>
    </button>
  );
});
