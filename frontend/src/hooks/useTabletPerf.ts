// ---------------------------------------------------------------------------
// useTabletPerf — Internal performance capability flags for tablet variant.
//
// Derives tablet-only rendering optimizations based on device detection.
// These flags are NEVER exposed to users — they activate automatically
// when the tablet variant (Fire HD / Fully Kiosk) is detected.
//
// Other variants (web, mobile, tv) always get DEFAULT_FLAGS (all false).
// ---------------------------------------------------------------------------

import { useMemo } from 'react';
import { useLayoutMode } from '@/contexts/LayoutModeContext';

interface TabletPerfFlags {
  /** True when running on tablet hardware. */
  isTablet: boolean;
  /** Prefer transform+opacity only animations; avoid layout-driven motion. */
  reduceMotion: boolean;
  /** Disable Ken Burns / continuous backdrop animation. */
  disableContinuousBackdropAnimation: boolean;
  /** Request lower-resolution backdrops (w1280 vs original). */
  limitBackdropResolution: boolean;
  /** Skip real-time blur(100px) on large layers; use gradient/color overlay. */
  avoidRealTimeBlur: boolean;
  /** Pause hero rotation and animated rails when modal is open. */
  pauseBackgroundAnimationsWhenModalOpen: boolean;
  /** Load trailers/embeds only after user interaction. */
  deferHeavyEmbeds: boolean;
  /** Reduce stacked heavy shadows and layered translucency. */
  reduceShadowComplexity: boolean;
}

const TABLET_FLAGS: TabletPerfFlags = {
  isTablet: true,
  reduceMotion: true,
  disableContinuousBackdropAnimation: true,
  limitBackdropResolution: false,
  avoidRealTimeBlur: true,
  pauseBackgroundAnimationsWhenModalOpen: true,
  deferHeavyEmbeds: true,
  reduceShadowComplexity: true,
};

const DEFAULT_FLAGS: TabletPerfFlags = {
  isTablet: false,
  reduceMotion: false,
  disableContinuousBackdropAnimation: false,
  limitBackdropResolution: false,
  avoidRealTimeBlur: false,
  pauseBackgroundAnimationsWhenModalOpen: false,
  deferHeavyEmbeds: false,
  reduceShadowComplexity: false,
};

export function useTabletPerf(): TabletPerfFlags {
  const { isTablet } = useLayoutMode();
  return useMemo(
    () => (isTablet ? TABLET_FLAGS : DEFAULT_FLAGS),
    [isTablet],
  );
}
