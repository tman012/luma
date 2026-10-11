/**
 * TVModeContext — global provider for TV Mode (Android TV / DPAD navigation).
 *
 * Detection priority:
 *   1. URL query param: ?mode=tv
 *   2. User agent: "LEANBACK" (Android TV WebView wrapper)
 *   3. localStorage flag: tv_mode=1
 *
 * When isTvMode is false, this provider is inert — no event listeners, no DOM changes.
 */

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useMemo,
  type ReactNode,
} from 'react';

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------

function detectTVMode(): boolean {
  if (typeof window === 'undefined') return false;

  // 1. URL query param
  const params = new URLSearchParams(window.location.search);
  if (params.get('mode') === 'tv' || params.get('tv') === '1') return true;

  // 2. User agent — Android TV wrapper includes LEANBACK
  if (/LEANBACK/i.test(navigator.userAgent)) return true;

  // 3. localStorage flag (set by settings toggle)
  if (localStorage.getItem('tv_mode') === '1') return true;

  return false;
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface TVModeContextValue {
  isTvMode: boolean;
  /** Enable/disable TV mode manually (persists to localStorage). */
  setTvMode: (on: boolean) => void;
}

const TVModeContext = createContext<TVModeContextValue>({
  isTvMode: false,
  setTvMode: () => {},
});

export function useTVMode(): TVModeContextValue {
  return useContext(TVModeContext);
}

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function TVModeProvider({ children }: { children: ReactNode }) {
  const [isTvMode, setIsTvMode] = useState(detectTVMode);

  // Add/remove kiosk-tv class on <html>
  useEffect(() => {
    if (isTvMode) {
      document.documentElement.classList.add('kiosk-tv');
      document.body.dataset.tvMode = 'true';
    } else {
      document.documentElement.classList.remove('kiosk-tv');
      delete document.body.dataset.tvMode;
    }
  }, [isTvMode]);

  const setTvMode = useMemo(
    () => (on: boolean) => {
      if (on) {
        localStorage.setItem('tv_mode', '1');
      } else {
        localStorage.removeItem('tv_mode');
      }
      setIsTvMode(on);
    },
    [],
  );

  const value = useMemo<TVModeContextValue>(
    () => ({ isTvMode, setTvMode }),
    [isTvMode, setTvMode],
  );

  return (
    <TVModeContext.Provider value={value}>
      {children}
    </TVModeContext.Provider>
  );
}
