/**
 * ExploreTile — platform-quality smart tile for Explore rails.
 *
 * Uses LANDSCAPE backdrop images (16:9) for an Apple TV / Google TV feel.
 * Title + year shown below the tile in faded text, left-aligned.
 *
 * Status orb (top-left, always visible):
 *   - Green: in Plex library
 *   - Blue: in watchlist (not yet in library)
 *   - Red: not in library
 *
 * Falls back to poster image if no backdrop available.
 */

import { memo, useState } from 'react';
import type { ExploreTileModel } from '@/types/explore';
import { cn } from '@/utils/cn';
import { getOrbColor } from '@/utils/orbColor';
import { useFocusable } from '@/hooks/useFocusable';
import { useUiDensity } from '@/hooks/useUiDensity';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const ExploreTile = memo(function ExploreTile({
  tile,
  onClick,
  railIndex,
  index,
}: {
  tile: ExploreTileModel;
  index: number;
  railIndex: number;
  onClick: () => void;
}) {
  const d = useUiDensity();
  const orb = getOrbColor({ isInPlex: tile.isInPlex, isInWatchlist: tile.isInWatchlist, ownerType: tile.ownerType, resolved: !!tile.ownershipKnown });
  const [imgLoaded, setImgLoaded] = useState(false);

  // Prefer backdrop (landscape) over poster (portrait)
  const imageUrl = tile.backdropUrl ?? tile.posterUrl;
  const usePoster = !tile.backdropUrl && !!tile.posterUrl;

  // TV mode: register as focusable for DPAD navigation
  const { ref: focusRef, focusProps } = useFocusable({
    focusKey: `tile-${railIndex}-${index}`,
    zoneId: `explore-rail-${railIndex}`,
    onEnter: onClick,
  });

  return (
    <button
      ref={focusRef as React.RefCallback<HTMLButtonElement>}
      type="button"
      onClick={onClick}
      data-explore-tile
      className={cn(
        'explore-tile group relative shrink-0 snap-start text-left',
        'outline-none focus-visible:ring-2 focus-visible:ring-accent/60',
        d.isTablet ? 'w-[320px]' : 'w-[420px]',
        focusProps.className,
      )}
      tabIndex={focusProps.tabIndex}
      data-focus-key={focusProps['data-focus-key']}
      aria-label={[
        tile.title,
        tile.isInPlex && 'in library',
        tile.isInWatchlist && 'in watchlist',
      ].filter(Boolean).join(', ')}
    >
      {/* Image tile */}
      <div
        className={cn(
          'relative overflow-hidden rounded-2xl',
          usePoster ? 'aspect-[2/3]' : 'aspect-video',
        )}
      >
        {imageUrl ? (
          <img
            src={imageUrl}
            alt=""
            loading="lazy"
            draggable={false}
            className={cn(
              'h-full w-full object-cover transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={(e) => {
              (e.target as HTMLImageElement).style.visibility = 'hidden';
            }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-glass-10 text-sm text-text-muted">
            No Image
          </div>
        )}

        {/* Hover lift */}
        <div className="absolute inset-0 bg-white/5 opacity-0 transition-opacity duration-200 group-hover:opacity-100 rounded-2xl" />

        {/* Status orb (top-left) — hidden until ownership is resolved */}
        {orb && (
          <div className="absolute left-3 top-3">
            <span
              className={cn('block h-3 w-3 rounded-full', orb.bg)}
              style={{ boxShadow: orb.glow }}
            />
          </div>
        )}
      </div>

      {/* Title + year below the tile */}
      <div className="mt-2 px-1">
        <p className="text-[17px] text-white/60 font-medium leading-snug truncate">
          {tile.title}
          {tile.year != null && (
            <span className="ml-1.5 text-white/40">({tile.year})</span>
          )}
        </p>
      </div>
    </button>
  );
});
