import { useEffect } from 'react';

/**
 * Enable body scroll for the lifetime of the component.
 *
 * On iOS with viewport-fit=cover, body scroll naturally extends into
 * the safe area (home indicator region). Fixed containers with
 * overflow-y:auto do NOT — they clip at the safe-area boundary,
 * leaving a visible bar at the bottom.
 *
 * This hook overrides the global `overflow: hidden` on html/body
 * (set in index.css) so the page content scrolls via the document
 * flow instead of a fixed container.
 *
 * v286: re-assert overflow:auto on visibilitychange. A stale sheet lock
 * cleared to empty (or left hidden across iOS resume) lets index.css
 * overflow:hidden win again; the visual viewport then rubber-bands and
 * the fixed Home header slides with the page.
 */
export function useBodyScroll(enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const html = document.documentElement;
    const body = document.body;
    const prevHtml = html.style.overflow;
    const prevBody = body.style.overflow;
    const unlock = () => {
      html.style.overflow = 'auto';
      body.style.overflow = 'auto';
    };
    unlock();
    const onVis = () => {
      if (document.visibilityState === 'visible') unlock();
    };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      html.style.overflow = prevHtml;
      body.style.overflow = prevBody;
    };
  }, [enabled]);
}
