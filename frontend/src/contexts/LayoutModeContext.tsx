/**
 * LayoutModeContext — single source of truth for layout detection.
 *
 * Two layout modes:
 *   - compact:  width < 768px (phone)
 *   - expanded: width >= 768px (tablet + desktop)
 *
 * Also exposes:
 *   - isTablet:      expanded + touch device (coarse pointer OR FullyKiosk UA)
 *   - isTouchDevice: pointer: coarse (any device)
 *
 * Sets CSS classes on <html>: .layout-compact OR .layout-expanded
 * Sets data-platform on <body>: 'fully' | 'webview' | 'desktop'
 *
 * TV mode is orthogonal — handled by TVModeContext (unchanged).
 */

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  type ReactNode,
} from 'react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type LayoutMode = 'compact' | 'expanded';

export interface LayoutModeValue {
  mode: LayoutMode;
  /** True when expanded + touch device (tablet hardware). */
  isTablet: boolean;
  /** True when pointer: coarse (any touch device). */
  isTouchDevice: boolean;
}

// ---------------------------------------------------------------------------
// Detection helpers
// ---------------------------------------------------------------------------

function isFullyKiosk(): boolean {
  if (typeof navigator === 'undefined') return false;
  return /FullyKiosk/i.test(navigator.userAgent);
}

function isCoarsePointer(): boolean {
  if (typeof matchMedia === 'undefined') return false;
  return matchMedia('(pointer: coarse)').matches;
}

function detectMode(): LayoutMode {
  if (typeof window === 'undefined') return 'expanded';
  return window.innerWidth < 768 ? 'compact' : 'expanded';
}

function detectIsTablet(mode: LayoutMode): boolean {
  if (mode === 'compact') return false;
  return isCoarsePointer() || isFullyKiosk();
}

function detectPlatform(): 'fully' | 'webview' | 'desktop' {
  if (typeof navigator === 'undefined') return 'desktop';
  const ua = navigator.userAgent;
  if (/FullyKiosk/i.test(ua)) return 'fully';
  if (/Android/i.test(ua) && (!/Chrome/i.test(ua) || /\bwv\b/i.test(ua))) return 'webview';
  return 'desktop';
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const LayoutModeContext = createContext<LayoutModeValue>({
  mode: 'expanded',
  isTablet: false,
  isTouchDevice: false,
});

export function useLayoutMode(): LayoutModeValue {
  return useContext(LayoutModeContext);
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function LayoutModeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<LayoutMode>(detectMode);
  const [isTablet, setIsTablet] = useState(() => detectIsTablet(detectMode()));
  const [isTouchDevice] = useState(isCoarsePointer);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const update = useCallback(() => {
    const newMode = detectMode();
    const newIsTablet = detectIsTablet(newMode);
    setMode(newMode);
    setIsTablet(newIsTablet);
  }, []);

  // Manage CSS classes on <html> and data-platform on <body>
  useEffect(() => {
    const root = document.documentElement;
    if (mode === 'compact') {
      root.classList.add('layout-compact');
      root.classList.remove('layout-expanded');
    } else {
      root.classList.add('layout-expanded');
      root.classList.remove('layout-compact');
    }
  }, [mode]);

  // Set platform data attribute once (UA doesn't change on resize)
  useEffect(() => {
    document.body.dataset.platform = detectPlatform();
  }, []);

  // Debounced resize listener
  useEffect(() => {
    update();

    const handleResize = () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(update, 200);
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [update]);

  const value = useMemo<LayoutModeValue>(
    () => ({ mode, isTablet, isTouchDevice }),
    [mode, isTablet, isTouchDevice],
  );

  return (
    <LayoutModeContext.Provider value={value}>
      {children}
    </LayoutModeContext.Provider>
  );
}
