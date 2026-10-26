import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Native-feel pull-to-refresh for document/body scroll pages (useBodyScroll).
 * Rubber-bands from the top; only a firm downward pull past the threshold
 * triggers refresh. Soft rail refetch isn't available on useHomeRails, so
 * callers typically reload after showing the spinner.
 *
 * Gesture rules:
 * - Only arms at (or very near) document scroll top.
 * - Locks out when horizontal movement dominates early, so home pills /
 *   rails can pan L/R without page bounce or preventDefault.
 * - Does not rubber-band or preventDefault while axis is undecided.
 * - NEVER engages when a modal/sheet/dialog owns the gesture, or when the
 *   touch starts inside a nested vertical scroller. Document-level
 *   touchmove + preventDefault would steal the first vertical pan from
 *   those surfaces (sticky scroll-from-rest on Collections, Activity,
 *   movie/TV modals, etc. while Home stays mounted underneath at scrollY≈0).
 */
const THRESHOLD = 92;
const MAX_PULL = 132;
const RESISTANCE = 0.42;
/** Ignore scroll jitter above top when deciding whether to arm. */
const TOP_SLOP_PX = 2;
/** Movement before we decide vertical vs horizontal axis. */
const AXIS_LOCK_PX = 10;
/** Absolute horizontal travel that aborts PTR even if dy is similar. */
const HORIZONTAL_ABORT_DX = 8;
/** Min downward travel before rubber-band / preventDefault engage. */
const VERTICAL_ENGAGE_DY = 10;

type AxisLock = 'undecided' | 'vertical' | 'horizontal';

export interface PullToRefreshState {
  /** Rubber-banded pull distance in px (0 when idle). */
  pullPx: number;
  /** True once the threshold was crossed and refresh started. */
  refreshing: boolean;
  /** 0..1 progress toward the refresh threshold (for indicator opacity). */
  progress: number;
}

/** True when nested UI should own touch — PTR must stay hands-off. */
/**
 * A scroll lock only counts if an overlay is really on screen. After iOS
 * resumes the PWA, a closed sheet can leave data-modal-open / overflow:hidden
 * behind, which silently killed pull-to-refresh until the app was force-closed.
 * Returns true when a stale lock was cleared.
 */
export function clearStaleModalLock(doc: Document = document): boolean {
  if (doc.querySelector('[data-modal], [data-sheet], [role="dialog"], [data-luma-overlay]')) return false;
  const root = doc.getElementById('root');
  const stale = !!root?.hasAttribute('data-modal-open')
    || doc.body.style.overflow === 'hidden'
    || doc.documentElement.style.overflow === 'hidden';
  root?.removeAttribute('data-modal-open');
  // Restore document scroll (useBodyScroll), not ''. Empty inline style lets
  // index.css `html, body { overflow: hidden }` win again — iOS then rubber-bands
  // the visual viewport so the fixed Home header slides with the page.
  if (doc.body.style.overflow === 'hidden') doc.body.style.overflow = 'auto';
  if (doc.documentElement.style.overflow === 'hidden') doc.documentElement.style.overflow = 'auto';
  return stale;
}

function touchOwnedByNestedScroller(e: TouchEvent): boolean {
  const root = document.getElementById('root');
  clearStaleModalLock();
  if (root?.hasAttribute('data-modal-open')) return true;

  // Sheets lock body overflow while Home remains mounted (often at scrollY 0).
  const bodyOy = document.body.style.overflow;
  const htmlOy = document.documentElement.style.overflow;
  if (bodyOy === 'hidden' || htmlOy === 'hidden') return true;

  const target = e.target;
  if (!(target instanceof Element)) return false;

  if (
    target.closest(
      '[data-scroll-container], [data-modal], [data-sheet], [role="dialog"], [data-luma-overlay]',
    )
  ) {
    return true;
  }

  // Fallback: unmarked nested *vertical* scrollers only.
  // Horizontal home rails set overflow-x:auto, and CSS then computes
  // overflow-y as auto too — those must NOT suppress document PTR.
  let el: Element | null = target;
  while (el && el !== document.body && el !== document.documentElement) {
    if (el instanceof HTMLElement) {
      const st = window.getComputedStyle(el);
      const oy = st.overflowY;
      if (
        (oy === 'auto' || oy === 'scroll' || oy === 'overlay') &&
        el.scrollHeight > el.clientHeight + 1
      ) {
        return true;
      }
    }
    el = el.parentElement;
  }
  return false;
}

export function usePullToRefresh(onRefresh: () => void, enabled = true): PullToRefreshState {
  const [pullPx, setPullPx] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const pullRef = useRef(0);
  const startX = useRef(0);
  const startY = useRef(0);
  const armed = useRef(false);
  const pulling = useRef(false);
  const axis = useRef<AxisLock>('undecided');
  const refreshingRef = useRef(false);
  const onRefreshRef = useRef(onRefresh);
  onRefreshRef.current = onRefresh;

  const setPull = useCallback((px: number) => {
    pullRef.current = px;
    setPullPx(px);
  }, []);

  useEffect(() => {
    if (!enabled) return;

    const scrollTop = () =>
      window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0;

    const resetGesture = () => {
      armed.current = false;
      pulling.current = false;
      axis.current = 'undecided';
      setPull(0);
    };

    const onStart = (e: TouchEvent) => {
      if (refreshingRef.current) return;
      if (touchOwnedByNestedScroller(e)) {
        resetGesture();
        return;
      }
      if (scrollTop() > TOP_SLOP_PX) {
        resetGesture();
        return;
      }
      const t = e.touches[0];
      startX.current = t?.clientX ?? 0;
      startY.current = t?.clientY ?? 0;
      armed.current = true;
      pulling.current = false;
      axis.current = 'undecided';
    };

    const onMove = (e: TouchEvent) => {
      if (!armed.current || refreshingRef.current) return;
      if (axis.current === 'horizontal') return;

      // Overlay may open mid-gesture; never preventDefault over nested UI.
      if (touchOwnedByNestedScroller(e)) {
        resetGesture();
        return;
      }

      if (scrollTop() > TOP_SLOP_PX) {
        resetGesture();
        return;
      }

      const t = e.touches[0];
      const x = t?.clientX ?? 0;
      const y = t?.clientY ?? 0;
      const dx = x - startX.current;
      const dy = y - startY.current;
      const absDx = Math.abs(dx);
      const absDy = Math.abs(dy);

      if (axis.current === 'undecided') {
        // Stay hands-off until the finger moves enough to pick an axis.
        if (absDx < AXIS_LOCK_PX && absDy < AXIS_LOCK_PX) return;

        // Horizontal wins early: abort PTR for this gesture.
        if (absDx > absDy || absDx >= HORIZONTAL_ABORT_DX) {
          axis.current = 'horizontal';
          armed.current = false;
          pulling.current = false;
          setPull(0);
          return;
        }

        // Need clear downward vertical dominance before engaging.
        if (dy <= 0 || absDy <= absDx) {
          armed.current = false;
          pulling.current = false;
          setPull(0);
          return;
        }

        axis.current = 'vertical';
      }

      // Vertical lock: only then rubber-band / preventDefault.
      if (dy <= 0) {
        pulling.current = false;
        setPull(0);
        return;
      }
      if (dy < VERTICAL_ENGAGE_DY && !pulling.current) return;

      pulling.current = true;
      const resisted = Math.min(dy * RESISTANCE, MAX_PULL);
      setPull(resisted);
      if (dy > VERTICAL_ENGAGE_DY && e.cancelable) e.preventDefault();
    };

    const onEnd = () => {
      if (!armed.current) {
        axis.current = 'undecided';
        pulling.current = false;
        return;
      }
      armed.current = false;
      const wasVertical = axis.current === 'vertical';
      axis.current = 'undecided';
      const px = pullRef.current;
      pulling.current = false;
      if (wasVertical && px >= THRESHOLD && !refreshingRef.current) {
        refreshingRef.current = true;
        setRefreshing(true);
        setPull(Math.max(px, 56));
        onRefreshRef.current();
        return;
      }
      setPull(0);
    };

    document.addEventListener('touchstart', onStart, { passive: true });
    document.addEventListener('touchmove', onMove, { passive: false });
    document.addEventListener('touchend', onEnd, { passive: true });
    document.addEventListener('touchcancel', onEnd, { passive: true });
    return () => {
      document.removeEventListener('touchstart', onStart);
      document.removeEventListener('touchmove', onMove);
      document.removeEventListener('touchend', onEnd);
      document.removeEventListener('touchcancel', onEnd);
    };
  }, [enabled, setPull]);

  return {
    pullPx,
    refreshing,
    progress: Math.min(1, pullPx / THRESHOLD),
  };
}
