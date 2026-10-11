import { useEffect } from 'react';

/**
 * Sets CSS custom properties on :root based on the Visual Viewport API.
 *
 * - --vvh: visible viewport height in px (falls back to window.innerHeight)
 * - --vvw: visible viewport width in px  (falls back to window.innerWidth)
 * - --vvoffsetTop: visual viewport offset from layout viewport top in px
 *
 * Android WebView / Fully Kiosk often report vh/dvh incorrectly.
 * The Visual Viewport API gives the *actual* visible rectangle.
 *
 * Debug overlay: set localStorage.setItem('debug_viewport', '1') and reload
 * to show a small readout of viewport values in the top-left corner.
 */
export function useVisualViewport(): void {
  useEffect(() => {
    const root = document.documentElement;
    let debugEl: HTMLDivElement | null = null;

    const showDebug = localStorage.getItem('debug_viewport') === '1';
    if (showDebug) {
      debugEl = document.createElement('div');
      Object.assign(debugEl.style, {
        position: 'fixed',
        top: '4px',
        left: '4px',
        zIndex: '99999',
        background: 'rgba(0,0,0,0.85)',
        color: '#0f0',
        fontFamily: 'monospace',
        fontSize: '11px',
        padding: '6px 10px',
        borderRadius: '6px',
        pointerEvents: 'none',
        lineHeight: '1.5',
        whiteSpace: 'pre',
      });
      document.body.appendChild(debugEl);
    }

    function update() {
      const vv = window.visualViewport;
      if (vv) {
        root.style.setProperty('--vvh', `${vv.height}px`);
        root.style.setProperty('--vvw', `${vv.width}px`);
        root.style.setProperty('--vvoffsetTop', `${vv.offsetTop}px`);
      } else {
        root.style.setProperty('--vvh', `${window.innerHeight}px`);
        root.style.setProperty('--vvw', `${window.innerWidth}px`);
        root.style.setProperty('--vvoffsetTop', '0px');
      }

      if (debugEl) {
        const computed = root.style.getPropertyValue('--vvh');
        debugEl.textContent =
          `innerH: ${window.innerHeight}\n` +
          `vv.height: ${vv ? vv.height : 'N/A'}\n` +
          `vv.offsetTop: ${vv ? vv.offsetTop : 'N/A'}\n` +
          `--vvh: ${computed}`;
      }
    }

    // Initial set
    update();

    // Listen for changes
    window.addEventListener('resize', update);
    window.addEventListener('orientationchange', update);

    const vv = window.visualViewport;
    if (vv) {
      vv.addEventListener('resize', update);
      vv.addEventListener('scroll', update);
    }

    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('orientationchange', update);
      if (vv) {
        vv.removeEventListener('resize', update);
        vv.removeEventListener('scroll', update);
      }
      if (debugEl) {
        debugEl.remove();
      }
    };
  }, []);
}
