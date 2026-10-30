import { useRef, useCallback, useEffect, type RefObject } from 'react';
import { MOTION } from '@/theme/motion';

/** Release dismisses past this share of the sheet height (or > 0.5 px/ms down). MOTION.md §1.5 */
const DISMISS_FRACTION = 0.33;

interface SwipeToDismissOptions {
  /** Element ref whose scrollTop is checked (modal scroll container) */
  scrollRef: RefObject<HTMLDivElement | null>;
  /** Called when swipe threshold is met */
  onDismiss: () => void;
  /** Whether swipe is enabled (e.g. only in 'visible' phase) */
  enabled?: boolean;
}

interface SwipeToDismissResult {
  /** Current translateY offset to merge with card transform */
  translateY: number;
  /** Backdrop opacity multiplier (1 = fully opaque, 0 = transparent) */
  backdropOpacity: number;
  /** Whether a swipe drag is actively in progress */
  isDragging: boolean;
  /** Transition string to apply (empty during drag, spring on snap-back) */
  transition: string;
  /** Touch handlers to spread on the scroll container */
  handlers: {
    onTouchStart: (e: React.TouchEvent) => void;
    onTouchMove: (e: React.TouchEvent) => void;
    onTouchEnd: () => void;
    onTouchCancel: () => void;
  };
}

/**
 * Hook that tracks downward swipe gestures on a modal scroll container.
 * Only activates when scrollTop === 0 and dragging downward.
 * Dismisses on velocity > 0.5px/ms OR distance > 33% of container height.
 *
 * Critical: never leave transform/tracking stuck — that traps native scroll
 * (especially "can't scroll back up" after a cancelled or interrupted gesture).
 */
export function useSwipeToDismiss({
  scrollRef,
  onDismiss,
  enabled = true,
}: SwipeToDismissOptions): SwipeToDismissResult {
  const stateRef = useRef({
    startY: 0,
    startX: 0,
    startTime: 0,
    currentY: 0,
    isDragging: false,
    isTracking: false,
    translateY: 0,
    backdropOpacity: 1,
    transition: '',
  });

  const frameRef = useRef(0);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => { timersRef.current.forEach(clearTimeout); }, []);

  const resetGesture = useCallback((opts?: { clearDom?: boolean }) => {
    const s = stateRef.current;
    s.isTracking = false;
    s.isDragging = false;
    s.translateY = 0;
    s.backdropOpacity = 1;
    s.transition = '';
    cancelAnimationFrame(frameRef.current);
    if (opts?.clearDom !== false) {
      const el = scrollRef.current;
      if (el) applyTransform(el, 0, 1, '');
    }
  }, [scrollRef]);

  // If swipe is disabled (nested sheet open) or unmounts mid-gesture, drop any
  // stuck card transform so the underlying modal can scroll again.
  useEffect(() => {
    if (!enabled) resetGesture();
  }, [enabled, resetGesture]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const el = scrollRef.current;
    // Always clear leftover drag from a cancelled prior gesture first —
    // otherwise a stale isTracking/transform blocks upward scroll.
    if (!enabledRef.current || !el || el.scrollTop > 0) {
      resetGesture();
      return;
    }

    const touch = e.touches[0]!;
    const s = stateRef.current;
    s.startY = touch.clientY;
    s.startX = touch.clientX;
    s.startTime = Date.now();
    s.isTracking = true;
    s.isDragging = false;
    s.translateY = 0;
    s.backdropOpacity = 1;
    s.transition = '';
  }, [scrollRef, resetGesture]);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!enabledRef.current) {
      resetGesture();
      return;
    }

    const s = stateRef.current;
    if (!s.isTracking) return;

    const el = scrollRef.current;
    if (!el) return;

    const touch = e.touches[0]!;
    const deltaY = touch.clientY - s.startY;
    const deltaX = touch.clientX - s.startX;

    // Content scrolled away from top — never fight native scroll.
    if (el.scrollTop > 0) {
      resetGesture();
      return;
    }

    // Upward / mostly-horizontal movement = native scroll, not dismiss.
    if (deltaY <= 0 || Math.abs(deltaX) > Math.abs(deltaY) + 6) {
      if (s.isDragging) {
        s.isDragging = false;
        s.translateY = 0;
        s.backdropOpacity = 1;
        applyTransform(el, 0, 1, '');
      }
      // Commit to native scroll once the finger clearly moves up.
      if (deltaY < -8) {
        s.isTracking = false;
      }
      return;
    }

    // Require intentional pull before capturing the gesture (avoids sticky scroll).
    const ACTIVATE_PX = 14;
    if (!s.isDragging && deltaY < ACTIVATE_PX) return;

    s.isDragging = true;
    s.currentY = touch.clientY;

    const dampened = deltaY * 0.6;
    s.translateY = dampened;

    const containerH = el.parentElement?.clientHeight ?? window.innerHeight;
    const progress = Math.min(dampened / (containerH * DISMISS_FRACTION), 1);
    s.backdropOpacity = 1 - progress * 0.7;

    // React's touchmove listener is passive, so preventDefault() here was a
    // no-op that only logged a warning; touch-action on the sheet handles it.

    cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(() => {
      applyTransform(el, dampened, s.backdropOpacity, '');
    });
  }, [scrollRef, resetGesture]);

  const finishGesture = useCallback(() => {
    const s = stateRef.current;
    if (!s.isTracking && !s.isDragging) return;
    const wasDragging = s.isDragging;
    s.isTracking = false;

    const el = scrollRef.current;
    if (!el || !wasDragging) {
      resetGesture();
      return;
    }

    const deltaY = s.currentY - s.startY;
    const elapsed = Date.now() - s.startTime;
    const velocity = elapsed > 0 ? deltaY / elapsed : 0;
    const containerH = el.parentElement?.clientHeight ?? window.innerHeight;
    const threshold = containerH * DISMISS_FRACTION;

    if (enabledRef.current && (velocity > 0.5 || s.translateY > threshold)) {
      s.isDragging = false;
      // Continue from where the finger left it: max(160, exit x remaining / height), accelerate out.
      const outMs = Math.round(Math.max(160, MOTION.dur.sheetExit * Math.max(0, containerH - s.translateY) / containerH));
      applyTransform(el, containerH, 0, `transform ${outMs}ms ${MOTION.ease.accel}`);
      timersRef.current.push(setTimeout(() => onDismiss(), outMs));
    } else {
      s.isDragging = false;
      s.translateY = 0;
      s.backdropOpacity = 1;
      applyTransform(el, 0, 1, `transform 300ms ${MOTION.ease.spring}`);

      timersRef.current.push(setTimeout(() => {
        if (el) applyTransform(el, 0, 1, '');
      }, 310));
    }
  }, [scrollRef, onDismiss, resetGesture]);

  const onTouchEnd = useCallback(() => {
    finishGesture();
  }, [finishGesture]);

  const onTouchCancel = useCallback(() => {
    // Interrupted gestures must never leave transform/preventDefault sticky.
    resetGesture();
  }, [resetGesture]);

  return {
    translateY: 0,
    backdropOpacity: 1,
    isDragging: false,
    transition: '',
    handlers: {
      onTouchStart,
      onTouchMove,
      onTouchEnd,
      onTouchCancel,
    },
  };
}

/** Apply transform + backdrop opacity directly to DOM for 60fps during drag */
function applyTransform(
  scrollEl: HTMLElement,
  translateY: number,
  backdropOpacity: number,
  transition: string,
) {
  const card = scrollEl.parentElement;
  if (card) {
    const base = 'translate3d(0, 0, 0)';
    card.style.transform = translateY > 0 ? `translate3d(0, ${translateY}px, 0)` : base;
    card.style.transition = transition ? transition : '';
  }

  const overlay = scrollEl.closest('[data-modal]') as HTMLElement | null;
  const scrim = overlay?.querySelector<HTMLElement>('[data-sheet-scrim]');
  if (scrim) scrim.style.opacity = String(MOTION.scrim * backdropOpacity);
  else if (overlay) {
    overlay.style.backgroundColor = `rgba(0,0,0,${MOTION.scrim * backdropOpacity})`;
  }
}
