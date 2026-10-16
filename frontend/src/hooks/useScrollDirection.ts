import { useState, useEffect, useRef, useCallback, type RefObject } from 'react';

interface UseScrollDirectionOptions {
  /** Scroll container ref to observe */
  scrollRef: RefObject<HTMLElement | null>;
  /** Pixel threshold for scroll-down hide (default: 5) */
  downThreshold?: number;
  /** Pixel threshold for scroll-up show (default: 3) */
  upThreshold?: number;
  /** Always show header when scrollTop is below this value (default: 60) */
  topZone?: number;
}

/**
 * Returns whether the header should be visible based on scroll direction.
 * Scroll down past threshold → hide; scroll up past threshold → show.
 * Always visible near the top of the scroll container.
 */
export function useScrollDirection({
  scrollRef,
  downThreshold = 5,
  upThreshold = 3,
  topZone = 60,
}: UseScrollDirectionOptions): boolean {
  const [visible, setVisible] = useState(true);
  const lastScrollTop = useRef(0);
  const rafId = useRef(0);

  const onScroll = useCallback(() => {
    cancelAnimationFrame(rafId.current);
    rafId.current = requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (!el) return;

      const scrollTop = el.scrollTop;

      // Always show near top
      if (scrollTop < topZone) {
        setVisible(true);
        lastScrollTop.current = scrollTop;
        return;
      }

      const delta = scrollTop - lastScrollTop.current;

      if (delta > downThreshold) {
        setVisible(false);
      } else if (delta < -upThreshold) {
        setVisible(true);
      }

      lastScrollTop.current = scrollTop;
    });
  }, [scrollRef, downThreshold, upThreshold, topZone]);

  useEffect(() => {
    // The observed element may not exist on the first commit (lazy/conditional
    // content). A ref's identity never changes when `.current` populates, so
    // retry each frame until the element mounts, then attach — otherwise the
    // listener would silently never bind.
    let el: HTMLElement | null = null;
    let attachRaf = 0;
    const attach = () => {
      el = scrollRef.current;
      if (!el) { attachRaf = requestAnimationFrame(attach); return; }
      el.addEventListener('scroll', onScroll, { passive: true });
    };
    attach();
    return () => {
      cancelAnimationFrame(attachRaf);
      if (el) el.removeEventListener('scroll', onScroll);
      cancelAnimationFrame(rafId.current);
    };
  }, [scrollRef, onScroll]);

  return visible;
}
