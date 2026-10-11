// ---------------------------------------------------------------------------
// useBackdropEffects — Reusable hook for Plex-like mobile modal backdrop
// effects: cover-cropped header + UltraBlur color-bleed background.
//
// Returns:
//   headerImgStyle      — CSS for the backdrop <img> (cover + zoom + position)
//   headerContainerMask — mask gradient for the backdrop header when UB active
//   hasUltraBlur        — whether palette data is available
// ---------------------------------------------------------------------------

import { useMemo } from 'react';
import type { UltraBlurColors } from '@/utils/modalParsers';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Post-cover zoom scale (1.0 = none). */
const BD_SCALE = 1.0;
/** Horizontal crop anchor (%). 50 = centered. */
const BD_POS_X = 50;
/** Vertical crop anchor (%). Lower = show more of the top of the image. */
const BD_POS_Y = 50;

/** Height of the backdrop header area as a CSS value. */
export const BACKDROP_HEIGHT = '50vh';

/** Numeric fallback (px) for contexts that need a number (e.g. scroll math).
 *  This is an approximation — actual height comes from the CSS value above. */
export const BACKDROP_HEIGHT_PX = 400;

// ---------------------------------------------------------------------------
// Return type
// ---------------------------------------------------------------------------

export interface BackdropEffects {
  /** CSS for the header backdrop <img> element. */
  headerImgStyle: React.CSSProperties;
  /** CSS properties for the backdrop header container. */
  headerContainerMask: React.CSSProperties;
  /** Whether UltraBlur palette data is available. */
  hasUltraBlur: boolean;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useBackdropEffects(
  _backdropUrl: string | null | undefined,
  ultraBlurColors: UltraBlurColors | null,
): BackdropEffects {
  const headerImgStyle: React.CSSProperties = useMemo(
    () => ({
      width: '100%',
      height: '100%',
      objectFit: 'cover' as const,
      objectPosition: `${BD_POS_X}% ${BD_POS_Y}%`,
      transform: `scale(${BD_SCALE})`,
      transformOrigin: `${BD_POS_X}% ${BD_POS_Y}%`,
    }),
    [],
  );

  const headerContainerMask: React.CSSProperties = useMemo(() => {
    return {
      WebkitMaskImage: 'linear-gradient(to bottom, black 50%, transparent 100%)',
      maskImage: 'linear-gradient(to bottom, black 50%, transparent 100%)',
    };
  }, []);

  return {
    headerImgStyle,
    headerContainerMask,
    hasUltraBlur: !!ultraBlurColors,
  };
}
