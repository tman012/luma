/**
 * Shared orb color logic for Explore tiles.
 *
 * Status orb colors:
 *   - Green: owned by current user (self)
 *   - Blue: owned by another user (shared) OR in watchlist
 *   - Red: not in any library
 */

export interface OrbColors {
  bg: string;
  glow: string;
}

export function getOrbColor(opts: {
  isInPlex?: boolean;
  isInWatchlist?: boolean;
  ownerType?: 'self' | 'shared';
  /**
   * Whether ownership/watchlist status has been resolved yet. When explicitly
   * false, returns null so the caller renders NO orb — prevents the misleading
   * "all red" flash before enrichment and hides orbs on library rails that
   * never enrich. Omitting it preserves the original always-return behavior.
   */
  resolved?: boolean;
}): OrbColors | null {
  if (opts.resolved === false) return null;
  if (opts.isInPlex) {
    if (opts.ownerType === 'shared') {
      return { bg: 'bg-blue-400', glow: '0 0 8px rgba(96,165,250,0.6)' };
    }
    return { bg: 'bg-green-400', glow: '0 0 8px rgba(74,222,128,0.6)' };
  }
  if (opts.isInWatchlist) {
    return { bg: 'bg-blue-400', glow: '0 0 8px rgba(96,165,250,0.6)' };
  }
  return { bg: 'bg-red-400', glow: '0 0 8px rgba(248,113,113,0.6)' };
}
