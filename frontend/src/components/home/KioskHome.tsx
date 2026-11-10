/** Web kiosk home (#12, >=1024px): prod's hero slideshow, display-only,
 * fed by the same /api/hero/content call (the selected library's most
 * recent movies). Full viewport, no scroll, no rails. The caller overlays
 * the logo + avatar header. Loading and empty are solid navy; loading adds
 * the kiosk spinner. */
import { useEffect, useState } from 'react';
import { HeroSection } from '@/components/media/HeroSection';
import { fetchHeroMovies } from './homeApi';
import type { HeroMovie } from '@/services/types';
import { decodeAll } from '@/startup/decode';
import { splashActive, splashRemainingMs } from '@/startup/splash';

export const KIOSK_MIN_WIDTH = 1024;
/** Without the startup splash, the longest the first backdrop's decode may hold Home. */
const KIOSK_BACKDROP_HOLD_MS = 1500;
const QUERY = `(min-width: ${KIOSK_MIN_WIDTH}px)`;

/** True at >=1024px wide, kept live as the window resizes. */
export function useKioskWidth(): boolean {
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.(QUERY).matches);
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(QUERY);
    const sync = () => setWide(mq.matches);
    sync();
    mq.addEventListener?.('change', sync);
    return () => mq.removeEventListener?.('change', sync);
  }, []);
  return wide;
}

export function KioskHome({ onReady }: { onReady?: () => void } = {}) {
  const [movies, setMovies] = useState<HeroMovie[] | null>(null);
  useEffect(() => {
    let alive = true;
    fetchHeroMovies()
      // Only items with a backdrop can be shown; none means empty (solid navy).
      .then(async list => {
        const shown = list.filter(m => !!m.backdropUrl);
        // The first backdrop decodes before Home shows (on startup the splash covers the wait, up to its cap), so it never fills in after.
        const first = shown[0]?.backdropUrl;
        if (onReady && first) await decodeAll([first], splashActive() ? splashRemainingMs() : KIOSK_BACKDROP_HOLD_MS);
        if (alive) { setMovies(shown); onReady?.(); }
      })
      .catch(() => { if (alive) { setMovies([]); onReady?.(); } });
    return () => { alive = false; };
  }, []);

  return (
    <div data-testid="kiosk-home" style={stage}>
      {movies === null ? (
        // Home's own startup path (onReady) shows no spinner: the splash, then the slideshow.
        onReady ? <div data-testid="kiosk-loading" style={center} aria-busy="true" /> : (
          <div data-testid="kiosk-loading" style={center} aria-busy="true">
            <div className="h-12 w-12 animate-spin rounded-full border-2 border-glass-20 border-t-accent" />
          </div>
        )
      ) : movies.length > 0 ? (
        <HeroSection movies={movies} displayOnly />
      ) : (
        <div data-testid="kiosk-empty" style={{ position: 'absolute', inset: 0 }} />
      )}
    </div>
  );
}

const stage: React.CSSProperties = {
  position: 'fixed', inset: 0, overflow: 'hidden', background: '#15102a',
};
const center: React.CSSProperties = {
  position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
};
