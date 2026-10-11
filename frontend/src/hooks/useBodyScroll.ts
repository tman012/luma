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
 */
export function useBodyScroll() {
  useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prevHtml = html.style.overflow;
    const prevBody = body.style.overflow;
    html.style.overflow = 'auto';
    body.style.overflow = 'auto';
    return () => {
      html.style.overflow = prevHtml;
      body.style.overflow = prevBody;
    };
  }, []);
}
