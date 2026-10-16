/**
 * useFocusable — per-element hook for TV Mode spatial navigation.
 *
 * Components call this to register themselves with the spatial nav system.
 * Returns a ref, focused state, and props to spread onto the element.
 *
 * Usage:
 *   const { ref, focused, focusProps } = useFocusable({
 *     focusKey: `tile-${id}`,
 *     zoneId: 'explore-rail-0',
 *     onEnter: () => handleClick(),
 *   });
 *   return <button ref={ref} {...focusProps}>...</button>;
 */

import { useRef, useEffect, useState, useCallback, useMemo } from 'react';
import { useTVMode } from '@/contexts/TVModeContext';
import type { SpatialNavManager } from './useSpatialNav';

// ---------------------------------------------------------------------------
// Shared context for the manager ref (set by SpatialNavProvider)
// ---------------------------------------------------------------------------

let _globalManager: SpatialNavManager | null = null;

/** Called by SpatialNavProvider to set the global manager ref. */
export function setSpatialNavManager(manager: SpatialNavManager | null) {
  _globalManager = manager;
}

/** Get the current spatial nav manager. */
export function getSpatialNavManager(): SpatialNavManager | null {
  return _globalManager;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

interface UseFocusableOptions {
  /** Unique identifier for this focusable element. */
  focusKey: string;
  /** Which FocusZone this element belongs to. */
  zoneId: string;
  /** Called when Enter is pressed while this element has focus. */
  onEnter?: () => void;
  /** Skip this element during navigation. */
  disabled?: boolean;
}

interface UseFocusableReturn {
  ref: React.RefCallback<HTMLElement>;
  /** True when this element has spatial focus. */
  focused: boolean;
  /** Props to spread onto the element. */
  focusProps: {
    'data-focus-key': string;
    tabIndex: number;
    className: string;
  };
}

export function useFocusable(options: UseFocusableOptions): UseFocusableReturn {
  const { focusKey, zoneId, onEnter, disabled } = options;
  const { isTvMode } = useTVMode();
  const elementRef = useRef<HTMLElement | null>(null);
  const [focused, setFocused] = useState(false);
  const onEnterRef = useRef(onEnter);
  onEnterRef.current = onEnter;

  // Ref callback for the element
  const ref = useCallback((node: HTMLElement | null) => {
    elementRef.current = node;
  }, []);

  // Register/unregister with the spatial nav manager
  useEffect(() => {
    if (!isTvMode || disabled || !elementRef.current) return;
    const manager = _globalManager;
    if (!manager) return;

    manager.registerElement(focusKey, zoneId, elementRef.current, () => {
      onEnterRef.current?.();
    });

    return () => {
      manager.unregisterElement(focusKey);
    };
  }, [isTvMode, focusKey, zoneId, disabled]);

  // Subscribe to focus changes
  useEffect(() => {
    if (!isTvMode) return;
    const manager = _globalManager;
    if (!manager) return;

    const unsub = manager.subscribe(() => {
      setFocused(manager.getFocusedKey() === focusKey);
    });
    // Check initial state
    setFocused(manager.getFocusedKey() === focusKey);
    return unsub;
  }, [isTvMode, focusKey]);

  const focusProps = useMemo(() => ({
    'data-focus-key': focusKey,
    // Roving tabindex: focused element gets 0, others get -1
    tabIndex: isTvMode ? (focused ? 0 : -1) : 0,
    className: isTvMode && focused ? 'tv-focus-ring' : '',
  }), [focusKey, isTvMode, focused]);

  return { ref, focused, focusProps };
}
