/**
 * FocusZone — container component for TV Mode focus groups.
 *
 * Wraps a group of focusable elements (a rail, a modal button bar, etc.)
 * and registers the zone with the spatial nav manager.
 *
 * Usage:
 *   <FocusZone zoneId="explore-rail-0" orientation="horizontal">
 *     {tiles.map(tile => <FocusableTile key={tile.id} ... />)}
 *   </FocusZone>
 */

import { useEffect, type ReactNode } from 'react';
import { useTVMode } from '@/contexts/TVModeContext';
import { getSpatialNavManager } from '@/hooks/useFocusable';
import type { ZoneOrientation } from '@/hooks/useSpatialNav';

export interface FocusZoneProps {
  /** Unique zone identifier. */
  zoneId: string;
  /** How elements are arranged: horizontal (rail), vertical (stack), grid. */
  orientation: ZoneOrientation;
  /** When true, DPAD navigation is trapped within this zone (for modals). */
  trapFocus?: boolean;
  /** Number of columns for grid orientation. */
  columns?: number;
  /** Called when Back/Escape is pressed while this zone is the active scope. */
  onEscape?: () => void;
  children: ReactNode;
  className?: string;
}

export function FocusZone({
  zoneId,
  orientation,
  trapFocus,
  columns,
  onEscape,
  children,
  className,
}: FocusZoneProps) {
  const { isTvMode } = useTVMode();

  // Register/unregister zone with the spatial nav manager
  useEffect(() => {
    if (!isTvMode) return;
    const manager = getSpatialNavManager();
    if (!manager) return;

    manager.registerZone(zoneId, {
      orientation,
      trapFocus,
      columns,
      onEscape,
    });

    // If this is a trap zone, push a focus scope
    if (trapFocus) {
      manager.pushFocusScope(zoneId);
    }

    return () => {
      if (trapFocus) {
        manager.popFocusScope();
      }
      manager.unregisterZone(zoneId);
    };
  }, [isTvMode, zoneId, orientation, trapFocus, columns, onEscape]);

  // In non-TV mode, just render children with no overhead
  if (className) {
    return <div className={className}>{children}</div>;
  }

  return <>{children}</>;
}
