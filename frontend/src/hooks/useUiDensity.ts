/**
 * useUiDensity — unified hook for UI density and device context.
 *
 * Combines layout mode (compact/expanded), tablet detection, touch detection,
 * and TV mode into a single density descriptor. Components use this with
 * uiDensityTokens() to get consistent sizing without ad-hoc isTablet ternaries.
 *
 * Density model:
 *   - compact:     phone (layoutMode.mode === 'compact')
 *   - comfortable: tablet, desktop, TV (everything else)
 *
 * Touch targets:
 *   - Touch devices (isTablet/isTouchDevice): 48px minimum
 *   - Mouse devices: 44px minimum
 */

import { useMemo } from 'react';
import { useLayoutMode, type LayoutMode } from '@/contexts/LayoutModeContext';
import { useTVMode } from '@/contexts/TVModeContext';

export interface UiDensity {
  mode: LayoutMode;
  /** True when expanded + touch device (tablet/kiosk hardware). */
  isTablet: boolean;
  /** True when pointer: coarse (any touch device). */
  isTouchDevice: boolean;
  /** True when TV/DPAD navigation mode is active. */
  isTvMode: boolean;
  /** Minimum touch/click target in px (48 for touch, 44 for mouse). */
  minTargetPx: 48 | 44;
  /** compact = phone, comfortable = tablet/desktop/TV. */
  density: 'compact' | 'comfortable';
}

export function useUiDensity(): UiDensity {
  const { mode, isTablet, isTouchDevice } = useLayoutMode();
  const { isTvMode } = useTVMode();

  return useMemo<UiDensity>(
    () => ({
      mode,
      isTablet,
      isTouchDevice,
      isTvMode,
      minTargetPx: isTouchDevice ? 48 : 44,
      density: mode === 'compact' ? 'compact' : 'comfortable',
    }),
    [mode, isTablet, isTouchDevice, isTvMode],
  );
}
