import { useRef, useCallback, useEffect, type RefObject } from 'react';

interface SwipeCarouselOptions {
  /** Ref to the carousel track container */
  containerRef: RefObject<HTMLDivElement | null>;
  totalSlides: number;
  activeIndex: number;
  onChangeIndex: (index: number) => void;
  onDismiss: () => void;
  enabled: boolean;
}

/**
 * Tinder-style horizontal swipe navigation between carousel cards,
 * with vertical swipe-to-dismiss (disambiguated via direction lock after 8px).
 * Touch-only — no mouse/desktop drag support.
 * All mid-drag transforms are applied directly to DOM for 60fps.
 */
export function useSwipeCarousel({
  containerRef,
  totalSlides,
  activeIndex,
  onChangeIndex,
  onDismiss,
  enabled,
}: SwipeCarouselOptions) {
  const stateRef = useRef({
    startX: 0,
    startY: 0,
    startTime: 0,
    currentX: 0,
    currentY: 0,
    direction: null as 'horizontal' | 'vertical' | null,
    isDragging: false,
  });
  const frameRef = useRef(0);
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Clear any pending dismiss timer / animation frame on unmount.
  useEffect(() => () => {
    if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
    cancelAnimationFrame(frameRef.current);
  }, []);

  /** Width of a single slide — uses first child to avoid flex container width ambiguity */
  const getSlideWidth = useCallback(() => {
    const track = containerRef.current;
    if (!track) return 0;
    return (track.firstElementChild as HTMLElement)?.offsetWidth ?? track.clientWidth;
  }, [containerRef]);

  // --- Shared drag logic ---

  const beginDrag = useCallback(
    (clientX: number, clientY: number) => {
      if (!enabled) return;
      const s = stateRef.current;
      s.startX = clientX;
      s.startY = clientY;
      s.currentX = clientX;
      s.currentY = clientY;
      s.startTime = Date.now();
      s.direction = null;
      s.isDragging = true;

      const track = containerRef.current;
      if (track) track.style.transition = 'none';
    },
    [enabled, containerRef],
  );

  const moveDrag = useCallback(
    (clientX: number, clientY: number, preventDefault?: () => void) => {
      const s = stateRef.current;
      if (!s.isDragging) return;
      const track = containerRef.current;
      if (!track) return;

      const dx = clientX - s.startX;
      const dy = clientY - s.startY;

      // Lock direction after 5px — low enough to feel instant
      if (!s.direction) {
        const absDx = Math.abs(dx);
        const absDy = Math.abs(dy);
        if (absDx > 5 || absDy > 5) {
          s.direction = absDy > absDx * 1.5 ? 'vertical' : 'horizontal';
        } else {
          // Still track position for smooth start, just don't commit direction yet
          s.currentX = clientX;
          s.currentY = clientY;
          return;
        }
      }

      s.currentX = clientX;
      s.currentY = clientY;

      if (s.direction === 'horizontal') {
        preventDefault?.();

        let offsetX = dx;
        // Rubberband at edges (50% damping)
        if ((activeIndex === 0 && dx > 0) || (activeIndex === totalSlides - 1 && dx < 0)) {
          offsetX = dx * 0.5;
        }

        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(() => {
          const baseTranslate = -(activeIndex * getSlideWidth());
          track.style.transform = `translate3d(${baseTranslate + offsetX}px, 0, 0)`;
        });
      } else if (s.direction === 'vertical' && dy > 0) {
        preventDefault?.();
        const dampened = dy * 0.6;

        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(() => {
          const overlay = track.closest('[data-carousel-overlay]') as HTMLElement | null;
          if (overlay) {
            const progress = Math.min(dampened / (window.innerHeight * 0.3), 1);
            overlay.style.backgroundColor = `rgba(0,0,0,${0.85 * (1 - progress * 0.7)})`;
          }
          track.style.transform = `translate3d(${-(activeIndex * getSlideWidth())}px, ${dampened}px, 0)`;
        });
      }
    },
    [containerRef, activeIndex, totalSlides, getSlideWidth],
  );

  const endDrag = useCallback(() => {
    const s = stateRef.current;
    if (!s.isDragging) return;
    s.isDragging = false;

    // Cancel any pending drag frame so it doesn't overwrite the snap transform
    cancelAnimationFrame(frameRef.current);

    const track = containerRef.current;
    if (!track) return;

    const dx = s.currentX - s.startX;
    const dy = s.currentY - s.startY;
    const elapsed = Date.now() - s.startTime;
    const width = getSlideWidth();

    if (s.direction === 'horizontal') {
      const velocity = elapsed > 0 ? Math.abs(dx) / elapsed : 0;
      let newIndex = activeIndex;

      // Tinder-style: quick flick (velocity) or deliberate drag (distance)
      const distanceThreshold = Math.min(width * 0.15, 35);
      if (velocity > 0.08 || Math.abs(dx) > distanceThreshold) {
        if (dx < 0 && activeIndex < totalSlides - 1) newIndex = activeIndex + 1;
        else if (dx > 0 && activeIndex > 0) newIndex = activeIndex - 1;
      }

      track.style.transition = 'transform 350ms cubic-bezier(0.32, 0.72, 0, 1)';
      track.style.transform = `translate3d(${-(newIndex * width)}px, 0, 0)`;

      if (newIndex !== activeIndex) {
        onChangeIndex(newIndex);
      }
    } else if (s.direction === 'vertical' && dy > 0) {
      const velocity = elapsed > 0 ? dy / elapsed : 0;
      const dampened = dy * 0.6;

      if (velocity > 0.5 || dampened > window.innerHeight * 0.3) {
        track.style.transition = 'transform 350ms cubic-bezier(0.32, 0.72, 0, 1)';
        track.style.transform = `translate3d(${-(activeIndex * width)}px, ${window.innerHeight}px, 0)`;

        const overlay = track.closest('[data-carousel-overlay]') as HTMLElement | null;
        if (overlay) {
          overlay.style.transition = 'background-color 300ms ease-out';
          overlay.style.backgroundColor = 'rgba(0,0,0,0)';
        }

        dismissTimerRef.current = setTimeout(() => onDismiss(), 280);
      } else {
        track.style.transition = 'transform 350ms cubic-bezier(0.32, 0.72, 0, 1)';
        track.style.transform = `translate3d(${-(activeIndex * width)}px, 0, 0)`;

        const overlay = track.closest('[data-carousel-overlay]') as HTMLElement | null;
        if (overlay) {
          overlay.style.transition = 'background-color 300ms ease-out';
          overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
        }
      }
    } else {
      track.style.transition = 'transform 350ms cubic-bezier(0.32, 0.72, 0, 1)';
      track.style.transform = `translate3d(${-(activeIndex * width)}px, 0, 0)`;
    }

    s.direction = null;
  }, [containerRef, activeIndex, totalSlides, onChangeIndex, onDismiss, getSlideWidth]);

  // --- Touch handlers ---

  const onTouchStart = useCallback(
    (e: React.TouchEvent) => {
      const touch = e.touches[0]!;
      beginDrag(touch.clientX, touch.clientY);
    },
    [beginDrag],
  );

  const onTouchMove = useCallback(
    (e: React.TouchEvent) => {
      const touch = e.touches[0]!;
      moveDrag(touch.clientX, touch.clientY, () => e.preventDefault());
    },
    [moveDrag],
  );

  const onTouchEnd = useCallback(() => {
    endDrag();
  }, [endDrag]);

  const onTouchCancel = useCallback(() => {
    endDrag();
  }, [endDrag]);

  // Sync track position when activeIndex changes externally (keyboard)
  useEffect(() => {
    const track = containerRef.current;
    if (!track) return;
    track.style.transition = 'transform 350ms cubic-bezier(0.32, 0.72, 0, 1)';
    track.style.transform = `translate3d(${-(activeIndex * getSlideWidth())}px, 0, 0)`;
  }, [activeIndex, containerRef, getSlideWidth]);

  // Handle resize — reposition track without animation
  useEffect(() => {
    const handleResize = () => {
      const track = containerRef.current;
      if (!track) return;
      track.style.transition = 'none';
      track.style.transform = `translate3d(${-(activeIndex * getSlideWidth())}px, 0, 0)`;
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [activeIndex, containerRef]);

  return {
    handlers: {
      onTouchStart,
      onTouchMove,
      onTouchEnd,
      onTouchCancel,
    },
  };
}
