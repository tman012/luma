/**
 * DownloadHistoryPage — Luma AI mode picker page.
 *
 * Shows the same mode cards as the Vibe Engine page (Feel Something, Pick For Me,
 * Describe It, Explore Visually) in a Watchlist-style page layout that handles
 * PWA safe areas correctly in compact mode.
 *
 * Tapping a card navigates to /library with a query param to auto-select the mode.
 */

import { useNavigate } from 'react-router';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { useUiDensity } from '@/hooks/useUiDensity';
import { cn } from '@/utils/cn';

// ---------------------------------------------------------------------------
// Mode card definitions (mirrored from VibeEnginePage)
// ---------------------------------------------------------------------------

const MODE_CARDS = [
  {
    id: 'feel',
    emoji: '\u{1F3AD}',
    title: 'Feel Something',
    subtitle: 'Browse by mood',
    gradient: 'from-purple-600/40 to-indigo-900/40',
    border: 'border-purple-500/30',
  },
  {
    id: 'pick',
    emoji: '\u{1F3B2}',
    title: 'Pick For Me',
    subtitle: 'Spin the roulette',
    gradient: 'from-amber-600/40 to-red-900/40',
    border: 'border-amber-500/30',
  },
  {
    id: 'describe',
    emoji: '\u{2728}',
    title: 'Describe It',
    subtitle: 'Tell me what you want',
    gradient: 'from-rose-600/40 to-pink-900/40',
    border: 'border-rose-500/30',
  },
  {
    id: 'explore',
    emoji: '\u{1FAE7}',
    title: 'Explore Visually',
    subtitle: 'Floating posters',
    gradient: 'from-cyan-600/40 to-teal-900/40',
    border: 'border-cyan-500/30',
  },
];

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export function DownloadHistoryPage() {
  useBodyScroll();
  const navigate = useNavigate();
  const d = useUiDensity();
  const isMobile = d.mode === 'compact';

  const handleCardClick = (modeId: string) => {
    if (modeId === 'describe') {
      navigate('/describe-it');
    } else {
      navigate(`/library?mode=${modeId}`);
    }
  };

  // ---------------------------------------------------------------------------
  // Shared content — mode cards
  // ---------------------------------------------------------------------------

  const content = (
    <>
      <style>{`
        @keyframes fadeSlideUp {
          0% { opacity: 0; transform: translateY(40px); }
          100% { opacity: 1; transform: translateY(0); }
        }
      `}</style>

      <p className="mb-6 text-[15px] text-text-muted">What are you in the mood for?</p>

      <div className={cn(
        'flex flex-col gap-4',
        !isMobile && 'sm:grid sm:grid-cols-2 sm:gap-6 lg:grid-cols-4',
      )}>
        {MODE_CARDS
          .filter((card) => !(isMobile && card.id === 'explore'))
          .map((card, idx) => (
          <button
            key={card.id}
            type="button"
            onClick={() => handleCardClick(card.id)}
            className={cn(
              'group relative flex items-center gap-5 rounded-2xl border backdrop-blur-md',
              'bg-gradient-to-br transition-all duration-300 px-5',
              'hover:scale-[1.02] hover:shadow-2xl active:scale-[0.98]',
              isMobile ? 'h-[88px]' : 'h-[160px] sm:flex-col sm:items-center sm:justify-center sm:gap-4 sm:px-4',
              card.gradient,
              card.border,
            )}
            style={{
              animation: `fadeSlideUp 0.5s ${0.1 + idx * 0.12}s cubic-bezier(0.34, 1.56, 0.64, 1) both`,
            }}
          >
            <span className={cn(
              'transition-transform duration-300 group-hover:scale-110',
              isMobile ? 'text-[40px]' : 'text-[48px]',
            )}>
              {card.emoji}
            </span>
            <div className={cn('flex flex-col', !isMobile && 'sm:items-center')}>
              <span className={cn(
                'font-semibold text-white',
                isMobile ? 'text-[20px]' : 'text-[22px]',
              )}>
                {card.title}
              </span>
              <span className={cn(
                'text-white/50',
                isMobile ? 'text-[14px]' : 'text-[14px]',
              )}>
                {card.subtitle}
              </span>
            </div>
          </button>
        ))}
      </div>
    </>
  );

  // ---------------------------------------------------------------------------
  // Mobile layout (mirrored from WatchlistPage)
  // ---------------------------------------------------------------------------

  if (isMobile) {
    return (
      <div
        className="text-white"
        style={{ minHeight: '100dvh', background: 'var(--color-kiosk-black)' }}
      >
        {/* Back button + title */}
        <div
          style={{
            position: 'fixed', top: 0, left: 0, right: 0, zIndex: 50,
            paddingTop: 'env(safe-area-inset-top, 0px)',
            background: 'var(--color-kiosk-black)',
          }}
        >
          <div style={{ height: 56, display: 'flex', alignItems: 'center', paddingLeft: 8, paddingRight: 16 }}>
            <button
              type="button"
              onClick={() => navigate('/')}
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

        {/* Content */}
        <div style={{ paddingTop: 'calc(56px + env(safe-area-inset-top, 0px) + 8px)', paddingLeft: 16, paddingRight: 16, paddingBottom: 32 }}>
          {content}
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Desktop / Tablet layout
  // ---------------------------------------------------------------------------

  return (
    <div data-scroll-container className="luma-scroll-y h-dvh bg-kiosk-black">
      <div className="mx-auto max-w-[1600px] pb-16 px-4 pt-16 sm:px-6 sm:pb-20 sm:pt-20 md:px-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-text-primary sm:text-3xl md:text-[36px]">Luma AI</h1>
        </div>
        {content}
      </div>
    </div>
  );
}
