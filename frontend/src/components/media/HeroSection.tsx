import { useState, useEffect, useRef, useCallback } from 'react';
import { cn } from '@/utils/cn';
import { formatRuntime } from '@/utils/image';
import { KenBurnsBackdrop } from './KenBurnsBackdrop';
import { useModalHost } from '@/contexts/ModalHostContext';
import { HeroOrb } from '@/components/voice/HeroOrb';
import { useFocusable } from '@/hooks/useFocusable';
import { FocusZone } from '@/components/tv/FocusZone';
import { useTabletPerf } from '@/hooks/useTabletPerf';
import { useUiDensity } from '@/hooks/useUiDensity';
import type { HeroMovie } from '@/services/types';

interface HeroSectionProps {
  movies: HeroMovie[];
  backdropSource?: 'plex' | 'tmdb';
  className?: string;
  /**
   * Web kiosk home (LUMA_MINIMAL, >=1024px): the same slideshow with nothing
   * to interact with. No click-to-open, swipe/drag, focus ring or voice orb;
   * rotation pauses while the tab is hidden and fades are instant under
   * reduced motion. Desktop sizing, text at white/90.
   */
  displayOnly?: boolean;
}

/** True while the page is hidden (another tab, minimized). */
function useDocumentHidden(enabled: boolean): boolean {
  const [hidden, setHidden] = useState(() => enabled && typeof document !== 'undefined' && document.hidden);
  useEffect(() => {
    if (!enabled) return;
    const sync = () => setHidden(document.hidden);
    sync();
    document.addEventListener('visibilitychange', sync);
    return () => document.removeEventListener('visibilitychange', sync);
  }, [enabled]);
  return enabled && hidden;
}

/** prefers-reduced-motion: reduce, kept live. */
function useReducedMotion(enabled: boolean): boolean {
  const query = '(prefers-reduced-motion: reduce)';
  const [reduced, setReduced] = useState(
    () => enabled && typeof window !== 'undefined' && !!window.matchMedia?.(query).matches,
  );
  useEffect(() => {
    if (!enabled || !window.matchMedia) return;
    const mq = window.matchMedia(query);
    const sync = () => setReduced(mq.matches);
    sync();
    mq.addEventListener?.('change', sync);
    return () => mq.removeEventListener?.('change', sync);
  }, [enabled]);
  return enabled && reduced;
}

/** Resolve the backdrop URL for a given movie based on source preference. */
function resolveBackdrop(
  movie: HeroMovie | undefined,
  backdropSource: 'plex' | 'tmdb',
): string | null {
  if (!movie) return null;
  if (backdropSource === 'tmdb' && movie.tmdbBackdropUrl) {
    return movie.tmdbBackdropUrl;
  }
  return movie.backdropUrl;
}

/** Preload an image URL. Resolves when loaded or after timeout. */
function preloadImage(url: string | null, timeoutMs = 1500): Promise<void> {
  if (!url) return Promise.resolve();
  return new Promise<void>((resolve) => {
    const img = new Image();
    const timer = setTimeout(resolve, timeoutMs);
    img.onload = () => { clearTimeout(timer); resolve(); };
    img.onerror = () => { clearTimeout(timer); resolve(); };
    img.src = url;
  });
}

/** Logo art when the title has one, else the title text. */
export function HeroSection({ movies, backdropSource = 'plex', className, displayOnly = false }: HeroSectionProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const activeIndexRef = useRef(0);
  activeIndexRef.current = activeIndex;
  const prevIndexRef = useRef(0);
  const transitioningRef = useRef(false);
  const dragStartXRef = useRef<number | null>(null);
  const isDraggingRef = useRef(false);

  const { openMovie } = useModalHost();
  const perf = useTabletPerf();
  const density = useUiDensity();
  const d = displayOnly ? { ...density, isTablet: false } : density;
  const pageHidden = useDocumentHidden(displayOnly);
  const reducedMotion = useReducedMotion(displayOnly);

  // Search is now hosted globally (GlobalSearchHost) and driven by the
  // `open-search` / `search-query` window events — no local overlay here.

  // Pause hero rotation when modal is open (tablet only — reduces background repainting)
  const [modalOpen, setModalOpen] = useState(false);
  useEffect(() => {
    if (!perf.pauseBackgroundAnimationsWhenModalOpen) return;
    const root = document.getElementById('root');
    if (!root) return;
    // Sync initial state
    setModalOpen(root.hasAttribute('data-modal-open'));
    const observer = new MutationObserver(() => {
      setModalOpen(root.hasAttribute('data-modal-open'));
    });
    observer.observe(root, { attributes: true, attributeFilter: ['data-modal-open'] });
    return () => observer.disconnect();
  }, [perf.pauseBackgroundAnimationsWhenModalOpen]);

  /** Advance to a new index after preloading its backdrop. */
  const advanceTo = useCallback(
    (nextIndex: number) => {
      if (transitioningRef.current) return;
      transitioningRef.current = true;

      const nextUrl = resolveBackdrop(movies[nextIndex], backdropSource);
      // The kiosk waits longer for the next backdrop so the crossfade never
      // lands on a layer whose image hasn't arrived yet.
      preloadImage(nextUrl, displayOnly ? 6000 : 1500).then(() => {
        setActiveIndex((prev) => {
          prevIndexRef.current = prev;
          return nextIndex;
        });
        transitioningRef.current = false;
      });
    },
    [movies, backdropSource, displayOnly],
  );

  // Auto-rotate every 8 seconds. Paused when modal is open on tablet.
  useEffect(() => {
    if (movies.length <= 1 || modalOpen || pageHidden) return;
    // Read the index from a ref; no side effects inside a state updater.
    const timer = setInterval(() => {
      advanceTo((activeIndexRef.current + 1) % movies.length);
    }, 8000);
    return () => clearInterval(timer);
  }, [movies.length, advanceTo, modalOpen, pageHidden]);

  if (movies.length === 0) return null;

  const movie = movies[activeIndex];
  if (!movie) return null;

  // Compute backdrop URLs from the SAME movie objects used for text/metadata.
  const currentBackdropUrl = resolveBackdrop(movie, backdropSource);
  const prevBackdropUrl = resolveBackdrop(movies[prevIndexRef.current], backdropSource);
  const hasMeta = Boolean(movie.year || (movie.duration > 0) || movie.contentRating);

  const handleHeroClick = () => {
    if (movie.ratingKey) {
      openMovie({
        ratingKey: movie.ratingKey,
        tmdbId: movie.tmdbId ?? undefined,
        origin: 'hero',
        onPersonSearch: (name, personId) => {
          window.dispatchEvent(new CustomEvent('search-query', { detail: { query: name, tab: 'people', personId } }));
        },
      });
    }
  };

  const { ref: heroRef, focusProps: heroFocusProps } = useFocusable({
    focusKey: 'hero-content',
    zoneId: 'home-hero',
    onEnter: handleHeroClick,
    disabled: displayOnly,
  });

  const handleDragStart = (x: number) => {
    dragStartXRef.current = x;
  };

  const handleDragEnd = (x: number) => {
    if (dragStartXRef.current === null || movies.length <= 1) return;
    const dx = x - dragStartXRef.current;
    dragStartXRef.current = null;
    isDraggingRef.current = false;
    if (Math.abs(dx) < 50) return; // minimum swipe distance
    if (dx < 0) {
      advanceTo((activeIndex + 1) % movies.length);
    } else {
      advanceTo((activeIndex - 1 + movies.length) % movies.length);
    }
  };

  const text = displayOnly ? 'text-white/90' : 'text-text-secondary';

  const backdrop = (
    <>
      {/* Static crossfade backdrop — Ken Burns pan/zoom disabled per kiosk pref */}
      <KenBurnsBackdrop
        currentImage={currentBackdropUrl}
        prevImage={prevBackdropUrl}
        disableAnimation
        instant={reducedMotion}
      />

      {/* Bottom gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-t from-kiosk-black via-kiosk-black/50 to-kiosk-black/20" />
    </>
  );

  const details = (
    <>
          {/* Title (the kiosk's only clickable spot: opens the details popup) */}
          {displayOnly ? (
            /* v241 (H2): the title as text, 96px regular; no clearlogo */
            <h1 data-hero-h2-title className="text-[#e2e2e9]"
              style={{ margin: 0, fontSize: 96, fontWeight: 400, letterSpacing: '-1.5px', lineHeight: 1 }}>
              <button
                type="button"
                data-hero-title
                onClick={handleHeroClick}
                className="pointer-events-auto cursor-pointer rounded-lg text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white"
                style={{ background: 'none', border: 0, padding: 0, font: 'inherit', color: 'inherit', letterSpacing: 'inherit' }}
              >
                {movie.title}
              </button>
            </h1>
          ) : (
            <h1 className={cn(
              'mb-4 font-bold leading-tight drop-shadow-2xl', text,
              d.isTablet ? 'text-[44px]' : 'text-[clamp(36px,3.2vw,60px)]',
            )}>
              {movie.title}
            </h1>
          )}

          {/* Meta row: Year · Duration · MPAA */}
          {(() => {
            const parts: string[] = [];
            if (movie.year) parts.push(String(movie.year));
            if (movie.duration > 0) parts.push(formatRuntime(movie.duration));
            if (movie.contentRating) parts.push(movie.contentRating);
            if (parts.length === 0) return null;
            if (displayOnly) {
              // v241 (H2): outlined chips, 16px above, 12px below
              return (
                <p data-hero-chips style={{ margin: '16px 0 12px', fontSize: 16 }}>
                  {parts.map(p => (
                    <span key={p} data-hero-chip
                      style={{ display: 'inline-block', border: '1px solid #8e9099', borderRadius: 8, padding: '4px 12px',
                        fontSize: 14, marginRight: 8, color: '#e2e2e9' }}>{p}</span>
                  ))}
                </p>
              );
            }
            return (
              <p className={cn(
                'mb-4 font-medium', text,
                d.isTablet ? 'text-[22px]' : 'text-[clamp(18px,1.6vw,24px)]',
              )}>
                {parts.join(' \u00B7 ')}
              </p>
            );
          })()}

          {/* Plot */}
          {movie.summary && displayOnly && (
            <p data-hero-plot style={{ margin: 0, fontSize: 18, color: '#e2e2e9', whiteSpace: 'nowrap', overflow: 'hidden',
              textOverflow: 'ellipsis', maxWidth: 720 }}>
              {movie.summary}
            </p>
          )}
          {movie.summary && !displayOnly && (
            <p className={cn(
              'max-w-[65%] leading-relaxed', text,
              d.isTablet
                ? (hasMeta ? 'text-[18px]' : 'text-[22px]')
                : 'text-[clamp(16px,1.3vw,20px)]',
            )}>
              {movie.summary}
            </p>
          )}

          {/* Genres */}
          {movie.genres && movie.genres.length > 0 && (
            <div className="mt-5 flex gap-3">
              {movie.genres.map((genre) => (
                <span
                  key={genre}
                  className={cn('rounded-full bg-glass-10 px-4 py-1.5 text-[16px]', displayOnly ? 'text-white/70' : 'text-text-muted')}
                >
                  {genre}
                </span>
              ))}
            </div>
          )}
    </>
  );

  if (displayOnly) {
    return (
      <section
        className={cn('relative h-dvh w-full select-none overflow-hidden', className)}
        data-hero-display-only
        aria-roledescription="slideshow"
      >
        {backdrop}
        <div data-hero-content className="pointer-events-none absolute bottom-[72px] left-[64px] z-10" style={{ maxWidth: 760 }}>{/* v241 (H2) */}
          {details}
        </div>
      </section>
    );
  }

  return (
    <section
      className={cn('relative h-dvh w-full overflow-hidden', className)}
      onTouchStart={(e) => handleDragStart(e.touches[0]?.clientX ?? 0)}
      onTouchEnd={(e) => handleDragEnd(e.changedTouches[0]?.clientX ?? 0)}
      onMouseDown={(e) => { isDraggingRef.current = true; handleDragStart(e.clientX); }}
      onMouseUp={(e) => { if (isDraggingRef.current) handleDragEnd(e.clientX); }}
      onMouseLeave={() => { dragStartXRef.current = null; isDraggingRef.current = false; }}
    >
      {backdrop}

      {/* Alfred orb — replaces search bar in expanded mode */}
      <HeroOrb />

      {/* Content overlay — bottom left, entire area clickable */}
      <FocusZone zoneId="home-hero" orientation="vertical">
        <div
          ref={heroRef as React.RefCallback<HTMLDivElement>}
          data-hero-content
          data-focus-key={heroFocusProps['data-focus-key']}
          className={cn(
            'absolute inset-x-0 bottom-0 z-10 cursor-pointer',
            d.isTablet ? 'px-[24px] pb-[24px]' : 'px-[60px] pb-[60px]',
            heroFocusProps.className,
          )}
          onClick={handleHeroClick}
          role="button"
          tabIndex={heroFocusProps.tabIndex}
        >
          {/* Tablet text legibility scrim — localized gradient behind text zone */}
          {d.isTablet && (
            <div
              className="absolute -left-[24px] -right-[24px] bottom-0 h-full pointer-events-none -z-[1]"
              style={{
                background: 'linear-gradient(to bottom, transparent 0%, rgba(0,0,0,0.12) 30%, rgba(0,0,0,0.45) 100%)',
              }}
            />
          )}

          {details}

        </div>
      </FocusZone>
    </section>
  );
}
