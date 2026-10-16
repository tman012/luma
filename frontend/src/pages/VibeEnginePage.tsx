/**
 * Vibe Engine — cinematic full-screen library experience.
 *
 * Three modes: Feel Something, Pick For Me, Explore Visually.
 * Uses Plex library data computed into mood-based vibes.
 *
 * Library is loaded lazily — only fetched when a mode is selected,
 * not on initial page load.
 */

import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react';
import { useNavigate, useLocation } from 'react-router';
import { useModalHost } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { FeelSomethingMode } from '@/pages/vibe/FeelSomethingMode';
import { PickForMeMode } from '@/pages/vibe/PickForMeMode';
import { fetchVibeLibrary, fetchVibeSettings } from '@/services/vibeApi';
import type { VibeSettings } from '@/services/vibeApi';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { cn } from '@/utils/cn';
import type { VibeItem, VibeMode } from '@/types/vibe';

const ExploreVisuallyMode = lazy(() =>
  import('@/pages/vibe/ExploreVisuallyMode').then((m) => ({ default: m.ExploreVisuallyMode })),
);

const DescribeMode = lazy(() =>
  import('@/pages/vibe/DescribeMode').then((m) => ({ default: m.DescribeMode })),
);

// ---------------------------------------------------------------------------
// Mode card definitions
// ---------------------------------------------------------------------------

const MODE_CARDS = [
  {
    id: 'feel' as const,
    emoji: '\u{1F3AD}',
    title: 'Feel Something',
    subtitle: 'Browse by mood',
    gradient: 'from-purple-600/40 to-indigo-900/40',
    border: 'border-purple-500/30',
  },
  {
    id: 'pick' as const,
    emoji: '\u{1F3B2}',
    title: 'Pick For Me',
    subtitle: 'Spin the roulette',
    gradient: 'from-amber-600/40 to-red-900/40',
    border: 'border-amber-500/30',
  },
  {
    id: 'explore' as const,
    emoji: '\u{1FAE7}',
    title: 'Explore Visually',
    subtitle: 'Floating posters',
    gradient: 'from-cyan-600/40 to-teal-900/40',
    border: 'border-cyan-500/30',
  },
  {
    id: 'describe' as const,
    emoji: '\u{2728}',
    title: 'Describe It',
    subtitle: 'Tell me what you want',
    gradient: 'from-rose-600/40 to-pink-900/40',
    border: 'border-rose-500/30',
  },
] as const;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface VibeEnginePageProps {
  /** When true, renders inside a modal card — uses relative positioning, not fixed */
  isModal?: boolean;
  /** Close callback when rendered as a modal */
  onClose?: () => void;
}

export function VibeEnginePage({ isModal = false, onClose }: VibeEnginePageProps = {}) {
  useBodyScroll();
  const navigate = useNavigate();
  const location = useLocation();
  const { mode: layoutMode } = useLayoutMode();
  const isMobile = layoutMode === 'compact';

  // Paint html background for mobile sheet — prevents safe-area bleed-through
  useEffect(() => {
    if (!isMobile) return;
    const prev = document.documentElement.style.background;
    document.documentElement.style.background = '#000';
    return () => { document.documentElement.style.background = prev; };
  }, [isMobile]);
  const [library, setLibrary] = useState<VibeItem[]>([]);
  const [libraryLoaded, setLibraryLoaded] = useState(false);
  const [, setLibraryLoading] = useState(false);
  const [mode, setMode] = useState<VibeMode>('home');
  const { openMovie, openTv, close } = useModalHost();
  const [vibeSettings, setVibeSettings] = useState<VibeSettings | null>(null);

  // Auto-enter describe mode from Alfred voice ("would you like to see results on Luma?")
  const [describeAutoQuery, setDescribeAutoQuery] = useState<string | null>(null);
  // Alfred: "I want something cozy" → /library?mood=cozy-comfort — jumps into Feel
  // mode with the mood preselected.
  const [initialVibeId, setInitialVibeId] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const desc = params.get('describe');
    if (desc) {
      setDescribeAutoQuery(desc);
      setMode('describe');
      navigate('/library', { replace: true });
      return;
    }
    // ?mood=<vibe-id> — jump into Feel Something with that mood preselected.
    const moodParam = params.get('mood');
    if (moodParam) {
      setInitialVibeId(moodParam);
      setMode('feel');
      navigate('/library', { replace: true });
      return;
    }
    // Auto-enter mode from ?mode= param (e.g. from Luma AI page pills)
    const modeParam = params.get('mode');
    if (modeParam && ['feel', 'pick', 'explore', 'describe'].includes(modeParam)) {
      setMode(modeParam as VibeMode);
      navigate('/library', { replace: true });
    }
  }, [location.search, navigate]);

  // Ref for DescribeMode's back handler — allows popstate to route
  // results→input before falling through to mode-level back.
  const describeBackRef = useRef<(() => boolean) | null>(null);

  // Track pushed history entries so we can clean up on unmount (prevents orphan entries)
  const historyPushCountRef = useRef(0);

  const handleBack = useCallback(() => {
    if (mode === 'home') {
      // Clear history push count BEFORE navigating so the cleanup effect
      // on unmount doesn't pop entries and re-trigger /library navigation.
      historyPushCountRef.current = 0;
      if (isModal && onClose) onClose();
      else navigate('/', { replace: true });
    } else {
      setMode('home');
    }
  }, [mode, isModal, onClose, navigate]);

  // History-based back interception for mobile PWA.
  // When in a sub-mode, we push a sentinel history entry so iOS's native
  // swipe-from-left-edge gesture (popstate) navigates back to the mode picker
  // instead of leaving the page. The snapshot iOS shows during the swipe animation
  // is the mode picker (captured at pushState time), so there's no flash.
  const modeRef = useRef(mode);
  modeRef.current = mode;

  useEffect(() => {
    if (!isMobile) return;

    // For route-based access (not modal), push initial sentinel for home state
    if (!isModal) {
      window.history.pushState({ vibeEngine: true }, '');
      historyPushCountRef.current++;
    }

    const handlePopState = () => {
      const currentMode = modeRef.current;

      // A history entry was consumed by the back navigation
      if (historyPushCountRef.current > 0) historyPushCountRef.current--;

      // Let DescribeMode handle sub-navigation first (results → input)
      if (currentMode === 'describe' && describeBackRef.current?.()) {
        // DescribeMode went from results/searching → input.
        // S1 (mode-level sentinel from selectMode) is still in history
        // for the next back gesture (input → home).
        return;
      }

      if (currentMode === 'home') {
        // Already at mode picker — dismiss
        historyPushCountRef.current = 0;
        if (isModal && onClose) onClose();
        else navigate('/', { replace: true });
      } else {
        // In a sub-mode — go back to mode picker
        setMode('home');
      }
    };

    window.addEventListener('popstate', handlePopState);
    return () => {
      window.removeEventListener('popstate', handlePopState);
      // Clean up orphan history entries pushed by this component.
      // This prevents history stack pollution when the modal is closed
      // via close button rather than back gesture.
      const count = historyPushCountRef.current;
      if (count > 0) {
        historyPushCountRef.current = 0;
        window.history.go(-count);
      }
    };
  }, [isMobile, isModal, navigate, onClose]);

  // Rotating backdrop (desktop only)
  const [backdropUrl, setBackdropUrl] = useState<string | null>(null);
  const backdropIdx = useRef(0);
  const backdropItems = useRef<VibeItem[]>([]);

  // Load library lazily — triggered when a mode is selected.
  // Returns a promise for the items so callers (Pick For Me) can wait for it
  // instead of spinning against an empty list. A failed or empty load is not
  // memoized, so the next call retries.
  const libraryItemsRef = useRef<VibeItem[]>([]);
  const libraryPromiseRef = useRef<Promise<VibeItem[]> | null>(null);
  const loadLibrary = useCallback((): Promise<VibeItem[]> => {
    if (libraryItemsRef.current.length > 0) return Promise.resolve(libraryItemsRef.current);
    if (libraryPromiseRef.current) return libraryPromiseRef.current;
    setLibraryLoading(true);
    // Rely on the service TTL cache — don't wipe it on every entry, or leaving
    // and re-entering Vibe pays a full Plex library refetch each time.
    const p = Promise.all([fetchVibeLibrary(), fetchVibeSettings()])
      .then(([items, settings]) => {
        if (items.length === 0) throw new Error('Vibe Engine: empty library');
        libraryItemsRef.current = items;
        setLibrary(items);
        setVibeSettings(settings);
        backdropItems.current = items.filter((i) => i.backdropUrl);
        if (backdropItems.current.length > 0) {
          setBackdropUrl(backdropItems.current[0]?.backdropUrl ?? null);
        }
        setLibraryLoaded(true);
        setLibraryLoading(false);
        return items;
      })
      .catch((err) => {
        console.error('Vibe Engine load error:', err);
        libraryPromiseRef.current = null;
        setLibraryLoading(false);
        setLibraryLoaded(true);
        throw err;
      });
    libraryPromiseRef.current = p;
    return p;
  }, []);

  // When a mode is selected, ensure library is loaded (describe & pick modes don't need it upfront)
  const selectMode = useCallback((m: VibeMode) => {
    setMode(m);
    if (m !== 'home' && m !== 'describe' && m !== 'pick') loadLibrary().catch(() => {});
    // Push history sentinel so iOS swipe-back returns to mode picker (not previous page)
    if (isMobile && m !== 'home') {
      window.history.pushState({ vibeEngine: true }, '');
      historyPushCountRef.current++;
    }
  }, [loadLibrary, isMobile]);

  // Rotate backdrop every 8 seconds (desktop only)
  useEffect(() => {
    if (isMobile || backdropItems.current.length < 2) return;
    const interval = setInterval(() => {
      backdropIdx.current = (backdropIdx.current + 1) % backdropItems.current.length;
      setBackdropUrl(backdropItems.current[backdropIdx.current]?.backdropUrl ?? null);
    }, 8000);
    return () => clearInterval(interval);
  }, [libraryLoaded, isMobile]);

  const handleOpenModal = useCallback((item: VibeItem) => {
    // Open detail modal on top — vibe engine stays open underneath.
    // AppModalHost renders detail modal with closeDetail when vibe engine is open,
    // so closing the detail modal returns to the carousel.
    openModalForItem(
      { openMovie, openTv },
      { mediaType: item.type, tmdbId: item.tmdbId, ratingKey: item.ratingKey > 0 ? item.ratingKey : undefined },
      'vibe_engine',
      {
        onPersonSearch: (name: string, personId?: number) => {
          close();
          const params = new URLSearchParams({ q: name, type: 'people' });
          if (personId) params.set('personId', String(personId));
          navigate(`/?${params.toString()}`);
        },
      },
    );
  }, [openMovie, openTv, close, navigate]);

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  // Show loading spinner when library is being fetched for a selected mode
  // Describe mode doesn't use the library (AI-only), so skip the loading state
  // Pick mode defers library load until user selects "In Your Library"
  const showModeLoading = mode !== 'home' && mode !== 'describe' && mode !== 'pick' && !libraryLoaded;

  // ---------------------------------------------------------------------------
  // Shared content — rendered inside both mobile sheet and desktop layouts
  // ---------------------------------------------------------------------------

  const vibeAnimations = (
    <style>{`
      @keyframes vibeEnter {
        0% { opacity: 0; transform: scale(0.96) translateY(20px); }
        100% { opacity: 1; transform: scale(1) translateY(0); }
      }
      @keyframes backdropPulse {
        0%, 100% { opacity: 0.35; }
        50% { opacity: 0.45; }
      }
      @keyframes fadeSlideUp {
        0% { opacity: 0; transform: translateY(40px); }
        100% { opacity: 1; transform: translateY(0); }
      }
      @keyframes cardHover {
        0%, 100% { transform: translateY(0); }
        50% { transform: translateY(-4px); }
      }
      @keyframes spin { to { transform: rotate(360deg); } }
    `}</style>
  );

  const vibeContent = (
    <>
      {/* Loading — shown when library is fetching for a selected mode */}
      {showModeLoading && (
        <div className="relative z-10 flex h-full flex-col items-center justify-center gap-4">
          <div
            className="h-12 w-12 rounded-full border-4 border-white/20 border-t-white"
            style={{ animation: 'spin 0.8s linear infinite' }}
          />
          <p className="text-lg text-white/60">Loading your library...</p>
        </div>
      )}

      {/* Home mode — mode cards (shown immediately, no library needed) */}
      {mode === 'home' && (
        <div
          className="relative z-10 flex h-full flex-col items-center justify-center px-5 sm:px-8"
          style={{ animation: 'vibeEnter 0.6s cubic-bezier(0.34, 1.56, 0.64, 1) forwards' }}
        >
          {/* Title */}
          <div className="mb-8 text-center sm:mb-12">
            <h1 className="text-[36px] font-bold tracking-tight text-white sm:text-[clamp(48px,5vw,80px)]">
              Luma AI
            </h1>
            <p className="mt-1 text-[15px] text-white/50 sm:mt-2 sm:text-[clamp(16px,1.8vw,24px)]">
              What are you in the mood for?
            </p>
          </div>

          {/* Mode cards */}
          <div className="flex w-full max-w-[340px] flex-col gap-4 sm:max-w-none sm:flex-row sm:gap-8 sm:justify-center">
            {MODE_CARDS
              .filter((card) => !(isMobile && card.id === 'explore'))
              .map((card, idx) => (
              <button
                key={card.id}
                type="button"
                onClick={() => selectMode(card.id)}
                className={cn(
                  'group relative flex items-center gap-5 sm:flex-col sm:items-center sm:justify-center sm:gap-4',
                  'h-[88px] sm:h-[320px] w-full sm:w-[280px] rounded-2xl sm:rounded-3xl border backdrop-blur-md',
                  'bg-gradient-to-br transition-all duration-300 px-5 sm:px-0',
                  'hover:scale-[1.02] sm:hover:scale-[1.04] hover:shadow-2xl active:scale-[0.98]',
                  card.gradient,
                  card.border,
                )}
                style={{
                  animation: `fadeSlideUp 0.5s ${0.1 + idx * 0.12}s cubic-bezier(0.34, 1.56, 0.64, 1) both`,
                }}
              >
                <span className="text-[40px] sm:text-[64px] transition-transform duration-300 group-hover:scale-110">
                  {card.emoji}
                </span>
                <div className="flex flex-col items-start sm:items-center">
                  <span className="text-[20px] sm:text-[28px] font-semibold text-white">
                    {card.title}
                  </span>
                  <span className="text-[14px] sm:text-[16px] text-white/50">
                    {card.subtitle}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Feel Something mode */}
      {mode === 'feel' && libraryLoaded && (
        <FeelSomethingMode
          library={library}
          onOpenModal={handleOpenModal}
          initialVibe={initialVibeId as any}
        />
      )}

      {/* Pick For Me mode — library loads on-demand when user selects "In Your Library" */}
      {mode === 'pick' && (
        <PickForMeMode
          library={library}
          onOpenModal={handleOpenModal}
          vibeSettings={vibeSettings}
          loadLibrary={loadLibrary}
        />
      )}

      {/* Explore Visually mode (desktop/tablet only) */}
      {mode === 'explore' && libraryLoaded && !isMobile && (
        <Suspense fallback={null}>
          <ExploreVisuallyMode library={library} onOpenModal={handleOpenModal} />
        </Suspense>
      )}

      {/* Describe It mode */}
      {mode === 'describe' && (
        <Suspense fallback={null}>
          <DescribeMode library={library} onOpenModal={handleOpenModal} isMobile={isMobile} backHandlerRef={describeBackRef} autoQuery={describeAutoQuery} onAutoQueryConsumed={() => setDescribeAutoQuery(null)} />
        </Suspense>
      )}
    </>
  );

  // ---------------------------------------------------------------------------
  // Mobile: full-page layout (matches Watchlist page structure)
  // ---------------------------------------------------------------------------

  if (isMobile) {
    return (
      <div
        className="flex flex-col text-white"
        style={{ height: '100dvh', background: 'var(--color-kiosk-black)', overflow: 'hidden' }}
      >
        {vibeAnimations}
        {/* Fixed header with safe area */}
        <div
          className="shrink-0"
          style={{
            paddingTop: 'env(safe-area-inset-top, 0px)',
            background: 'var(--color-kiosk-black)',
          }}
        >
          <div style={{ height: 56, display: 'flex', alignItems: 'center', paddingLeft: 8, paddingRight: 16 }}>
            <button
              type="button"
              onClick={handleBack}
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 44, height: 44, background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'rgba(255,255,255,0.7)' }}
              aria-label="Back"
            >
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15 18 9 12 15 6" />
              </svg>
            </button>
            <h1 style={{ margin: 0, flex: 1, fontSize: 18, fontWeight: 600, color: 'white', textAlign: 'center', marginRight: 44 }}>Luma AI</h1>
          </div>
        </div>

        {/* Content — flex-1 gives children a definite height for h-full */}
        <div data-scroll-container className="luma-scroll-y flex flex-1 flex-col" style={{ paddingBottom: 'max(32px, env(safe-area-inset-bottom, 0px))' }}>
          {vibeContent}
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Desktop / Tablet / Modal layout (unchanged)
  // ---------------------------------------------------------------------------

  return (
    <div className={cn(
      'overflow-hidden',
      isModal ? 'h-full relative' : 'h-dvh',
      'bg-kiosk-black',
    )}>
      {vibeAnimations}

      {/* Animated backdrop — desktop/tablet only */}
      {backdropUrl && (
        <div className={cn('inset-0 z-0', isModal ? 'absolute' : 'fixed')}>
          <img
            key={backdropUrl}
            src={backdropUrl}
            alt=""
            className="h-full w-full object-cover transition-opacity duration-[2000ms]"
            style={{ animation: 'backdropPulse 8s ease-in-out infinite' }}
          />
          {/* Dark overlay + blur */}
          <div className="absolute inset-0 bg-kiosk-black/75 backdrop-blur-xl" />
          {/* Bottom gradient */}
          <div className="absolute inset-0 bg-gradient-to-t from-kiosk-black via-kiosk-black/40 to-transparent" />
        </div>
      )}

      {/* Back button */}
      <button
        type="button"
        onClick={() => {
          if (mode === 'home') {
            if (isModal && onClose) onClose();
            else navigate('/');
          } else {
            setMode('home');
          }
        }}
        className={cn(
          'left-4 top-4 z-50 flex h-11 w-11 items-center justify-center rounded-full border-none bg-black/50 text-white backdrop-blur-md transition-transform active:scale-90 sm:left-6 sm:top-6 sm:h-14 sm:w-14',
          isModal ? 'absolute' : 'fixed',
        )}
        aria-label="Back"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="sm:h-7 sm:w-7">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>

      {vibeContent}
    </div>
  );
}
