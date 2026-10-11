/**
 * useSpatialNav — core spatial navigation engine for TV Mode DPAD navigation.
 *
 * Manages a registry of focusable zones (rails, grids, modal panels) and
 * individual focusable elements within them. On DPAD press, computes the
 * nearest neighbor in the pressed direction via bounding-rect geometry.
 *
 * Key concepts:
 *   - Zone: a group of focusable elements (horizontal rail, vertical stack, grid)
 *   - Element: a single focusable item (card, button, nav link)
 *   - Focus scope stack: for modal trapping (push on open, pop on close)
 */

import { useRef, useEffect } from 'react';
import { useTVMode } from '@/contexts/TVModeContext';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ZoneOrientation = 'horizontal' | 'vertical' | 'grid';

export interface ZoneOptions {
  orientation: ZoneOrientation;
  trapFocus?: boolean;
  columns?: number;
  /** Called when Back/Escape pressed while this zone is the active scope. */
  onEscape?: () => void;
}

interface RegisteredZone {
  id: string;
  options: ZoneOptions;
}

interface RegisteredElement {
  key: string;
  zoneId: string;
  element: HTMLElement;
  onEnter?: () => void;
}

interface FocusScope {
  zoneId: string;
  previousFocusKey: string | null;
}

// ---------------------------------------------------------------------------
// Spatial nav manager (imperative, not reactive)
// ---------------------------------------------------------------------------

export class SpatialNavManager {
  private zones = new Map<string, RegisteredZone>();
  private elements = new Map<string, RegisteredElement>();
  private focusedKey: string | null = null;
  private lastContentFocusKey: string | null = null;
  private scopeStack: FocusScope[] = [];
  private listeners = new Set<() => void>();
  private bootFocusRaf: number | null = null;

  // -- Zone registration --

  registerZone(id: string, options: ZoneOptions) {
    this.zones.set(id, { id, options });
  }

  unregisterZone(id: string) {
    this.zones.delete(id);
    // Remove all elements in that zone
    for (const [key, el] of this.elements) {
      if (el.zoneId === id) this.elements.delete(key);
    }
  }

  // -- Element registration --

  registerElement(key: string, zoneId: string, element: HTMLElement, onEnter?: () => void) {
    this.elements.set(key, { key, zoneId, element, onEnter });

    // Boot focus: if nothing focused yet, schedule auto-focus after all
    // elements in this render batch have registered (rAF coalesces).
    if (!this.focusedKey) {
      this.scheduleBootFocus();
    }
  }

  private scheduleBootFocus() {
    if (this.bootFocusRaf) return;
    this.bootFocusRaf = requestAnimationFrame(() => {
      this.bootFocusRaf = null;
      if (this.focusedKey) return; // already focused
      const first = this.getFirstAvailable();
      if (first) {
        this.setFocus(first.key);
      } else {
        // Fallback: focus first DOM-focusable element
        const fallback = document.querySelector<HTMLElement>(
          'button, a[href], [tabindex="0"], input, select, textarea'
        );
        if (fallback) {
          fallback.focus();
        }
      }
    });
  }

  unregisterElement(key: string) {
    this.elements.delete(key);
    if (this.focusedKey === key) {
      this.focusedKey = null;
    }
  }

  // -- Focus state --

  getFocusedKey(): string | null {
    return this.focusedKey;
  }

  setFocus(key: string) {
    const el = this.elements.get(key);
    if (!el) return;
    this.focusedKey = key;
    // Track the last focused content element (anything outside TopBar)
    if (el.zoneId !== 'topbar-nav') {
      this.lastContentFocusKey = key;
    }
    el.element.focus({ preventScroll: true });
    el.element.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    this.notify();
  }

  // -- Focus scope stack (modal trapping) --

  pushFocusScope(zoneId: string) {
    this.scopeStack.push({
      zoneId,
      previousFocusKey: this.focusedKey,
    });
    // Focus first element in the new scope
    const first = this.getElementsInZone(zoneId)[0];
    if (first) this.setFocus(first.key);
  }

  popFocusScope(): string | null {
    const scope = this.scopeStack.pop();
    if (!scope) return null;
    // Restore previous focus
    if (scope.previousFocusKey) {
      this.setFocus(scope.previousFocusKey);
    }
    return scope.previousFocusKey;
  }

  // -- Navigation --

  moveFocus(direction: 'up' | 'down' | 'left' | 'right') {
    const currentEl = this.focusedKey ? this.elements.get(this.focusedKey) : null;

    if (!currentEl) {
      // No focus — focus first available element
      const first = this.getFirstAvailable();
      if (first) this.setFocus(first.key);
      return;
    }

    // TopBar → Down: restore last content focus or find first content element
    if (direction === 'down' && currentEl.zoneId === 'topbar-nav') {
      if (this.lastContentFocusKey && this.elements.has(this.lastContentFocusKey)) {
        this.setFocus(this.lastContentFocusKey);
      } else {
        const first = Array.from(this.elements.values())
          .filter((e) => e.zoneId !== 'topbar-nav')
          .sort((a, b) => {
            const aRect = a.element.getBoundingClientRect();
            const bRect = b.element.getBoundingClientRect();
            if (Math.abs(aRect.top - bRect.top) > 10) return aRect.top - bRect.top;
            return aRect.left - bRect.left;
          })[0];
        if (first) this.setFocus(first.key);
      }
      return;
    }

    // Get the active scope zone (if any)
    const activeScope = this.scopeStack.length > 0
      ? this.scopeStack[this.scopeStack.length - 1]!
      : null;

    const currentRect = currentEl.element.getBoundingClientRect();
    let candidates: RegisteredElement[];

    if (activeScope?.zoneId) {
      // Trapped: only consider elements in the scope zone
      candidates = this.getElementsInZone(activeScope.zoneId)
        .filter((e) => e.key !== this.focusedKey);
    } else {
      // Free: consider all elements but prefer same zone for primary direction
      candidates = Array.from(this.elements.values())
        .filter((e) => e.key !== this.focusedKey);
    }

    // Filter candidates by direction
    const inDirection = candidates.filter((c) => {
      const rect = c.element.getBoundingClientRect();
      return isInDirection(currentRect, rect, direction);
    });

    if (inDirection.length === 0) return;

    // Find nearest by distance
    const nearest = inDirection.reduce((best, candidate) => {
      const bestDist = spatialDistance(currentRect, best.element.getBoundingClientRect(), direction);
      const candDist = spatialDistance(currentRect, candidate.element.getBoundingClientRect(), direction);
      return candDist < bestDist ? candidate : best;
    });

    this.setFocus(nearest.key);
  }

  activateFocused() {
    if (!this.focusedKey) return;
    const el = this.elements.get(this.focusedKey);
    if (!el) return;
    if (el.onEnter) {
      el.onEnter();
    } else {
      el.element.click();
    }
  }

  goBack() {
    // If there's a focus scope, call its onEscape
    if (this.scopeStack.length > 0) {
      const scope = this.scopeStack[this.scopeStack.length - 1]!;
      const zone = this.zones.get(scope.zoneId);
      if (zone?.options.onEscape) {
        zone.options.onEscape();
        return true;
      }
    }
    return false;
  }

  // -- Subscription for re-renders --

  subscribe(listener: () => void) {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private notify() {
    for (const listener of this.listeners) listener();
  }

  // -- Helpers --

  private getElementsInZone(zoneId: string): RegisteredElement[] {
    return Array.from(this.elements.values())
      .filter((e) => e.zoneId === zoneId)
      .sort((a, b) => {
        const aRect = a.element.getBoundingClientRect();
        const bRect = b.element.getBoundingClientRect();
        // Sort by position: top-to-bottom, left-to-right
        if (Math.abs(aRect.top - bRect.top) > 10) return aRect.top - bRect.top;
        return aRect.left - bRect.left;
      });
  }

  private getFirstAvailable(): RegisteredElement | null {
    const activeScope = this.scopeStack.length > 0
      ? this.scopeStack[this.scopeStack.length - 1]!
      : null;
    if (activeScope) {
      return this.getElementsInZone(activeScope.zoneId)[0] ?? null;
    }
    const all = Array.from(this.elements.values());
    if (all.length === 0) return null;
    return all.sort((a, b) => {
      const aRect = a.element.getBoundingClientRect();
      const bRect = b.element.getBoundingClientRect();
      if (Math.abs(aRect.top - bRect.top) > 10) return aRect.top - bRect.top;
      return aRect.left - bRect.left;
    })[0] ?? null;
  }
}

// ---------------------------------------------------------------------------
// Direction + distance helpers
// ---------------------------------------------------------------------------

function isInDirection(from: DOMRect, to: DOMRect, direction: 'up' | 'down' | 'left' | 'right'): boolean {
  const fromCenter = { x: from.left + from.width / 2, y: from.top + from.height / 2 };
  const toCenter = { x: to.left + to.width / 2, y: to.top + to.height / 2 };

  switch (direction) {
    case 'up':    return toCenter.y < fromCenter.y - 5;
    case 'down':  return toCenter.y > fromCenter.y + 5;
    case 'left':  return toCenter.x < fromCenter.x - 5;
    case 'right': return toCenter.x > fromCenter.x + 5;
  }
}

function spatialDistance(from: DOMRect, to: DOMRect, direction: 'up' | 'down' | 'left' | 'right'): number {
  const fromCenter = { x: from.left + from.width / 2, y: from.top + from.height / 2 };
  const toCenter = { x: to.left + to.width / 2, y: to.top + to.height / 2 };

  const dx = toCenter.x - fromCenter.x;
  const dy = toCenter.y - fromCenter.y;

  // Primary axis distance gets full weight, cross-axis gets penalty
  const isHorizontal = direction === 'left' || direction === 'right';
  const primary = isHorizontal ? Math.abs(dx) : Math.abs(dy);
  const cross = isHorizontal ? Math.abs(dy) : Math.abs(dx);

  // Heavily penalize cross-axis distance to prefer items in the same row/column
  return primary + cross * 3;
}

// ---------------------------------------------------------------------------
// React hook — creates a singleton manager per provider
// ---------------------------------------------------------------------------

export function useSpatialNav(): SpatialNavManager | null {
  const { isTvMode } = useTVMode();
  const managerRef = useRef<SpatialNavManager | null>(null);

  if (isTvMode && !managerRef.current) {
    managerRef.current = new SpatialNavManager();
  }

  // Global keydown handler for DPAD navigation
  useEffect(() => {
    if (!isTvMode || !managerRef.current) return;

    const manager = managerRef.current;

    function handleKeyDown(e: KeyboardEvent) {
      // Don't interfere with text inputs
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
        // Allow Escape to blur the input
        if (e.key === 'Escape') {
          (e.target as HTMLElement).blur();
          e.preventDefault();
        }
        return;
      }

      switch (e.key) {
        case 'ArrowUp':
          e.preventDefault();
          manager.moveFocus('up');
          break;
        case 'ArrowDown':
          e.preventDefault();
          manager.moveFocus('down');
          break;
        case 'ArrowLeft':
          e.preventDefault();
          manager.moveFocus('left');
          break;
        case 'ArrowRight':
          e.preventDefault();
          manager.moveFocus('right');
          break;
        case 'Enter':
          e.preventDefault();
          manager.activateFocused();
          break;
        case 'Escape':
        case 'Backspace': {
          // Back button on Android TV remote maps to Backspace or Escape
          const handled = manager.goBack();
          if (handled) {
            e.preventDefault();
            e.stopPropagation();
          }
          // If not handled, let the existing Escape handlers (modal close) run
          break;
        }
      }
    }

    // Use capture phase so we get first crack at the events
    window.addEventListener('keydown', handleKeyDown, true);
    return () => window.removeEventListener('keydown', handleKeyDown, true);
  }, [isTvMode]);

  return isTvMode ? managerRef.current : null;
}
