import { lazy, Suspense, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router';
import { useVisualViewport } from '@/hooks/useVisualViewport';
import { TVModeProvider } from '@/contexts/TVModeContext';
import { LayoutModeProvider, useLayoutMode } from '@/contexts/LayoutModeContext';
import { SpatialNavProvider } from '@/components/tv/SpatialNavProvider';
import { AppModalHost } from '@/contexts/AppModalHost';
import { KioskLayout } from '@/components/layout/KioskLayout';
import { HomeKioskPage } from '@/pages/HomeKioskPage';
import { TopBar } from '@/components/layout/TopBar';
import { GlobalSearchHost } from '@/components/search/GlobalSearchHost';
import { NotificationsProvider } from '@/contexts/NotificationsContext';
import { useKioskMode, invalidateKioskMode } from '@/hooks/useKioskMode';
import { PageModal } from '@/components/layout/PageModal';
import { ScreensaverManager } from '@/components/screensaver/ScreensaverManager';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { VoiceProvider } from '@/contexts/VoiceContext';
import { LoginPage } from '@/pages/LoginPage';
import { AdminPage } from '@/pages/AdminPage';
import { UserSetupPage } from '@/pages/UserSetupPage';
import { LandingPage } from '@/pages/LandingPage';
import { RatePage } from '@/pages/RatePage';
import { TastePromptHost } from '@/components/taste/TastePromptHost';
import { AuthenticatedHomePage } from '@/pages/AuthenticatedHomePage';
import { StartPage } from '@/pages/StartPage';
import { UnlockPage } from '@/pages/UnlockPage';
import { ConnectServiceHost } from '@/components/connect/ConnectServiceHost';
import { apiFetch } from './apiClient';
import { isMinimal } from './minimal';
import { MinimalHomePage } from '@/pages/MinimalHomePage';
import { SetupToast } from '@/components/start/SetupToast';
import { getUrlBase } from '@/urlHelpers';

// Lazy-load non-home routes — keeps initial bundle small
const ExploreKioskPage = lazy(() => import('@/pages/ExploreKioskPage').then(m => ({ default: m.ExploreKioskPage })));
const VibeEnginePage = lazy(() => import('@/pages/VibeEnginePage').then(m => ({ default: m.VibeEnginePage })));
const NowAvailablePage = lazy(() => import('@/pages/NowAvailablePage').then(m => ({ default: m.NowAvailablePage })));
const WatchlistPage = lazy(() => import('@/pages/WatchlistPage').then(m => ({ default: m.WatchlistPage })));
const DownloadHistoryPage = lazy(() => import('@/pages/DownloadHistoryPage').then(m => ({ default: m.DownloadHistoryPage })));
const DescribeItPage = lazy(() => import('@/pages/DescribeItPage').then(m => ({ default: m.DescribeItPage })));
const BrowseLibraryPage = lazy(() => import('@/pages/BrowseLibraryPage').then(m => ({ default: m.BrowseLibraryPage })));
const SettingsPage = lazy(() => import('@/pages/SettingsPage').then(m => ({ default: m.SettingsPage })));
const MySettingsPage = lazy(() => import('@/pages/MySettingsPage').then(m => ({ default: m.MySettingsPage })));

/**
 * AuthGate — redirects unauthenticated users to /login.
 * Renders nothing while the auth state is loading.
 */
function signInPath(needsStart: boolean, pinUnlock: boolean) {
  return needsStart ? '/start' : pinUnlock ? '/unlock' : '/login';
}

function AuthGate({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, needsStart, pinUnlock } = useAuth();

  if (loading) {
    return null;
  }

  if (!authenticated) {
    return <Navigate to={signInPath(needsStart, pinUnlock)} replace />;
  }

  return <>{children}</>;
}

/**
 * AdminGate — redirects non-admin users to home.
 */
function AdminGate({ children }: { children: React.ReactNode }) {
  const { authenticated, loading, user, needsStart, pinUnlock } = useAuth();

  if (loading) return null;
  if (!authenticated) return <Navigate to={signInPath(needsStart, pinUnlock)} replace />;
  if (!user?.is_admin) return <Navigate to="/" replace />;

  return <>{children}</>;
}

/**
 * Subtle floating pill to toggle between Movie kiosk and Daily kiosk modes.
 * Saves the new mode to settings and reloads to apply it.
 */
function KioskModeToggle() {
  const kioskMode = useKioskMode();
  const [pending, setPending] = useState(false);

  if (kioskMode === null) return null;

  const isDaily = kioskMode === 'daily';

  const toggle = async () => {
    if (pending) return;
    const newMode = isDaily ? 'movie' : 'daily';
    setPending(true);
    try {
      await apiFetch('/api/kiosk/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kiosk_mode: newMode }),
      });
      invalidateKioskMode();
      window.location.reload();
    } catch {
      setPending(false);
    }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={pending}
      title={`Switch to ${isDaily ? 'Movie' : 'Daily'} kiosk`}
      style={{
        position: 'fixed',
        bottom: 22,
        ...(isDaily ? { left: 22 } : { right: 22 }),
        zIndex: 50,
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        background: 'rgba(255,255,255,0.09)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        border: '1px solid rgba(255,255,255,0.14)',
        borderRadius: 100,
        padding: '8px 18px 8px 14px',
        cursor: pending ? 'default' : 'pointer',
        opacity: pending ? 0.4 : 0.65,
        transition: 'opacity 0.2s',
      }}
      onMouseEnter={(e) => { if (!pending) (e.currentTarget as HTMLElement).style.opacity = '1'; }}
      onMouseLeave={(e) => { if (!pending) (e.currentTarget as HTMLElement).style.opacity = '0.65'; }}
    >
      {/* Toggle track */}
      <div style={{
        width: 38,
        height: 22,
        borderRadius: 100,
        background: isDaily ? 'rgba(139,92,246,0.9)' : 'rgba(255,255,255,0.25)',
        position: 'relative',
        transition: 'background 0.25s',
        flexShrink: 0,
      }}>
        <div style={{
          position: 'absolute',
          top: 3,
          left: isDaily ? 19 : 3,
          width: 16,
          height: 16,
          borderRadius: '50%',
          background: 'white',
          transition: 'left 0.25s cubic-bezier(0.16, 1, 0.3, 1)',
        }} />
      </div>
      <span style={{
        fontSize: 13,
        fontWeight: 600,
        color: 'rgba(255,255,255,0.85)',
        letterSpacing: '0.05em',
        textTransform: 'uppercase',
        userSelect: 'none',
      }}>
        {isDaily ? 'Daily' : 'Movie'}
      </span>
    </button>
  );
}

/**
 * Home component — conditionally renders based on auth + layout mode.
 *
 * - Not authenticated → LandingPage
 * - Authenticated + expanded (desktop/tablet) → full kiosk experience (TopBar + Hero)
 * - Authenticated + compact (mobile) → AuthenticatedHomePage (rails + inline search)
 */
function Home() {
  const { authenticated, loading, needsSetup, needsStart, startInProgress, pinUnlock } = useAuth();
  const { mode } = useLayoutMode();
  const kioskMode = useKioskMode();

  if (loading) {
    return null;
  }

  if (authenticated && startInProgress) {
    return <Navigate to="/start" replace />;
  }

  if (authenticated) {
    if (needsSetup) {
      return <Navigate to="/setup" replace />;
    }
    if (mode === 'expanded') {
      const isDaily = kioskMode === 'daily';
      // Desktop/tablet — full kiosk layout with TopBar nav + hero section
      return (
        <div className="relative min-h-dvh bg-kiosk-black">
          {!isDaily && <TopBar />}
          <main>
            <HomeKioskPage />
          </main>
          <ScreensaverManager />
          <KioskModeToggle />
        </div>
      );
    }
    return (
      <div className="relative min-h-dvh" style={{ background: 'var(--color-kiosk-black)' }}>
        <AuthenticatedHomePage />
      </div>
    );
  }

  if (needsStart) return <Navigate to="/start" replace />;
  if (pinUnlock) return <Navigate to="/unlock" replace />;
  return <LandingPage />;
}

/** Router basename from the runtime URL base, so navigation keeps it. */
export function routerBasename(): string | undefined {
  const base = getUrlBase().replace(/\/+$/, '');
  return base || undefined;
}

/** LUMA_MINIMAL=1: Get Started, sign-in and the main screen only. */
function MinimalApp() {
  return (
    <BrowserRouter basename={routerBasename()}>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<MinimalHomePage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/start" element={<StartPage />} />
          <Route path="/unlock" element={<UnlockPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <SetupToast />
      </AuthProvider>
    </BrowserRouter>
  );
}

export default function App() {
  useVisualViewport();

  if (isMinimal()) return <MinimalApp />;

  return (
    <BrowserRouter basename={routerBasename()}>
      <AuthProvider>
        <TVModeProvider>
          <LayoutModeProvider>
            <SpatialNavProvider>
              <AppModalHost>
                <VoiceProvider>
                <NotificationsProvider>
                {/* Always-mounted search overlay so the TopBar search works on
                    every page/state (was in HeroSection → dead when no Hero). */}
                <GlobalSearchHost />
                {/* Also always-mounted, and for the same reason: the kiosk home
                    page is served by the `/` route, which is a sibling of
                    AppRoutes — anything mounted inside AppRoutes is dead on the
                    one screen this prompt most needs to appear on. */}
                <TastePromptHost />
                <ConnectServiceHost />
                <Suspense fallback={null}>
                  <Routes>
                    {/* Home — conditional: PublicHomePage if not auth, AuthenticatedHomePage if auth */}
                    <Route path="/" element={<Home />} />
                    <Route path="/login" element={<LoginPage />} />
                    <Route path="/start" element={<StartPage />} />
                    <Route path="/unlock" element={<UnlockPage />} />

                    {/* Setup — requires auth */}
                    <Route
                      path="/setup"
                      element={
                        <AuthGate>
                          <UserSetupPage />
                        </AuthGate>
                      }
                    />

                    {/* Admin panel — requires auth (AdminPage redirects non-admins) */}
                    <Route
                      path="/admin"
                      element={
                        <AuthGate>
                          <AdminPage />
                        </AuthGate>
                      }
                    />

                    {/* All other routes require auth */}
                    <Route
                      path="/*"
                      element={
                        <AuthGate>
                          <AppRoutes />
                        </AuthGate>
                      }
                    />
                  </Routes>
                </Suspense>
                <SetupToast />
                </NotificationsProvider>
                </VoiceProvider>
                {/* ScreensaverManager outside auth gate — harmless when not logged in */}
              </AppModalHost>
            </SpatialNavProvider>
          </LayoutModeProvider>
        </TVModeProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}

/** Inner routes — only rendered when authenticated. */
function AppRoutes() {
  const { mode } = useLayoutMode();
  const isExpanded = mode === 'expanded';
  const kioskMode = useKioskMode();
  const isDaily = kioskMode === 'daily';

  // In expanded mode, always render home underneath with page modals on top.
  // In compact mode, keep full-screen page routing.
  return (
    <>
      {/* Home backdrop — always visible in expanded mode so modals overlay it */}
      {isExpanded && (
        <div className="relative min-h-dvh bg-kiosk-black">
          {!isDaily && <TopBar />}
          <main>
            <HomeKioskPage />
          </main>
        </div>
      )}

      <Suspense fallback={null}>
        <Routes>
          {/* Pages — wrapped in PageModal for expanded mode */}
          <Route path="explore" element={isExpanded ? <PageModal><ExploreKioskPage /></PageModal> : <ExploreKioskPage />} />
          <Route path="library" element={isExpanded ? <Navigate to="/browse" replace /> : <VibeEnginePage />} />
          <Route path="rooms" element={<Navigate to="/" replace />} />
          <Route path="now-available" element={isExpanded ? <PageModal><NowAvailablePage /></PageModal> : <NowAvailablePage />} />
          <Route path="watchlist" element={isExpanded ? <PageModal><WatchlistPage /></PageModal> : <WatchlistPage />} />
          <Route path="downloads" element={isExpanded ? <PageModal><DownloadHistoryPage /></PageModal> : <DownloadHistoryPage />} />
          <Route path="describe-it" element={isExpanded ? <PageModal><DescribeItPage /></PageModal> : <DescribeItPage />} />
          <Route path="browse" element={isExpanded ? <PageModal><BrowseLibraryPage /></PageModal> : <BrowseLibraryPage />} />
          <Route path="upgrades" element={<Navigate to="/" replace />} />
          <Route path="settings" element={isExpanded ? <PageModal><AdminGate><SettingsPage /></AdminGate></PageModal> : <AdminGate><SettingsPage /></AdminGate>} />
          <Route path="my-settings" element={isExpanded ? <PageModal><MySettingsPage /></PageModal> : <MySettingsPage />} />
          {/* Rate — the deep-link target of the "rate this" push notification.
              Wrapped in PageModal on desktop for consistency, but it is designed
              for the phone, which is where the notification is tapped. */}
          <Route path="rate/:tmdbId" element={isExpanded ? <PageModal><RatePage /></PageModal> : <RatePage />} />

          {/* Fallback */}
          {!isExpanded && (
            <Route element={<KioskLayout />}>
              <Route index element={<HomeKioskPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          )}
          {isExpanded && <Route path="*" element={<Navigate to="/" replace />} />}
        </Routes>
      </Suspense>
      <ScreensaverManager />
    </>
  );
}
