import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { MobileHomePage } from './MobileHomePage';
import { HeroSection } from '@/components/media/HeroSection';
import { ExploreGrid } from '@/components/media/ExploreGrid';
import { useHeroContent } from '@/hooks/useHeroContent';
import { useExploreMovies } from '@/hooks/useExploreMovies';
import { DailyKioskPage } from './DailyKioskPage';
import { useKioskMode } from '@/hooks/useKioskMode';

export function HomeKioskPage() {
  const { mode } = useLayoutMode();

  if (mode === 'compact') return <MobileHomePage />;

  return <DesktopHome />;
}

function DesktopHome() {
  const kioskMode = useKioskMode();

  // Don't render until we know the mode
  if (kioskMode === null) {
    return (
      <div className="flex h-dvh items-center justify-center">
        <div className="h-12 w-12 animate-spin rounded-full border-2 border-glass-20 border-t-accent" />
      </div>
    );
  }

  if (kioskMode === 'daily') {
    return <DailyKioskPage />;
  }

  return <MovieKioskHome />;
}

function MovieKioskHome() {
  const { movies: heroMovies, loading: heroLoading, backdropSource } = useHeroContent();
  const { movies: exploreMovies, loading: exploreLoading } = useExploreMovies();

  return (
    <div className="relative">
      {/* Hero section - full viewport */}
      {!heroLoading && heroMovies.length > 0 && (
        <HeroSection movies={heroMovies} backdropSource={backdropSource} />
      )}

      {/* Loading state */}
      {heroLoading && (
        <div className="flex h-dvh items-center justify-center">
          <div className="h-12 w-12 animate-spin rounded-full border-2 border-glass-20 border-t-accent" />
        </div>
      )}

      {/* Explore section */}
      {!exploreLoading && exploreMovies.length > 0 && (
        <section className="relative z-10 mt-8 pb-20">
          <h2 className="mb-6 px-[60px] text-[32px] font-semibold text-text-primary">
            Explore
          </h2>
          <ExploreGrid movies={exploreMovies} />
        </section>
      )}

      {exploreLoading && (
        <div className="flex items-center justify-center py-20">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-glass-20 border-t-accent" />
        </div>
      )}
    </div>
  );
}
