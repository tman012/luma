/** Main screen of a LUMA_MINIMAL build: the phone-first Home (#11). */
import { useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { FONT, LOGO_SRC, NAVY_BG } from '@/components/start/StartUI';
import { assetUrl } from '@/urlHelpers';
import { HomeRails, SkeletonRails, cardIds, railCss } from '@/components/home/Rails';
import { openWhenReady } from '@/services/modalPrefetch';
import { useModalHost } from '@/contexts/ModalHostContext';
import type { Card } from '@/components/home/homeApi';
import { LUMA_BLUE } from '@/ui/lumaButton';
import { fetchHome, type HomeData } from '@/components/home/homeApi';
import { KioskHome, useKioskWidth } from '@/components/home/KioskHome';
import { UserMenu } from '@/components/nav/UserMenu';
import { openSearch, SearchIcon } from '@/components/search/MinimalSearchHost';
import { HomeSearchR3 } from '@/components/search/HomeSearchR3';

export function MinimalHomePage() {
  const { authenticated, loading, needsStart, startInProgress, pinUnlock, user, implicit, logout } = useAuth();
  const navigate = useNavigate();
  // >=1024px: the web kiosk (one full-screen slideshow, no scroll). Phones
  // and small tablets keep the rails, which scroll the page: index.css locks
  // html/body scrolling, so unlock it there.
  const kiosk = useKioskWidth();
  useBodyScroll(!kiosk);
  const [busy, setBusy] = useState(false);
  const [data, setData] = useState<HomeData | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [slow, setSlow] = useState(false);
  const modals = useModalHost();
  // Phone rails: the popup opens only once it can show fully composed (the
  // tapped poster shows a pressed state meanwhile, 600 ms cap).
  const openCard = (c: Card, el?: HTMLElement | null) => {
    const { kind, ratingKey, tmdbId } = cardIds(c);
    void openWhenReady(kind, ratingKey, tmdbId, el ?? null, () => openDetail(modals, c));
  };

  const ready = !loading && authenticated && !startInProgress;
  useEffect(() => {
    if (!ready || kiosk) return;                // v235: KioskHome loads its own hero and rails (as before v228)
    let alive = true;
    setFailed(false);
    setSlow(false);
    setData(null);
    // A skeleton that's still up after 8 s means Plex isn't answering.
    const timer = window.setTimeout(() => { if (alive) setSlow(true); }, SKELETON_TIMEOUT_MS);
    // One request returns every rail, so all of them appear together.
    fetchHome().then(d => { if (alive) setData(d); }).catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; window.clearTimeout(timer); };
  }, [ready, attempt, kiosk]);

  if (loading) return null;
  if (!authenticated) return <Navigate to={needsStart ? '/start' : pinUnlock ? '/unlock' : '/login'} replace />;
  if (startInProgress) return <Navigate to="/start" replace />;

  const signOut = async () => {
    setBusy(true);
    await logout();
    navigate('/login', { replace: true });
  };

  // Home has only the logo and the avatar; the avatar menu holds Settings (admins) and Sign out.
  const menu = (size: number) => !implicit ? (
    <UserMenu name={user?.name || ''} admin={!!user?.is_admin} busy={busy} onSignOut={signOut} size={size} />
  ) : <span style={{ width: size }} />;

  if (kiosk) {
    return (
      <div data-step="home" data-layout="kiosk">
        <KioskHome />
        {/* v235: logo and avatar; the search field is centered in this bar (HomeSearchR3). */}
        <header style={kioskBar} data-testid="kiosk-header">
          <img src={assetUrl(LOGO_SRC)} alt="Luma" data-testid="kiosk-logo" style={{ height: 64, width: 'auto' }} draggable={false} />
          {menu(48)}
        </header>
        <HomeSearchR3 />
      </div>
    );
  }

  return (
    <div style={page} data-step="home">
      <style>{railCss}</style>
      <div data-shift-root>
        <header style={phoneBar}>
          <img src={assetUrl(LOGO_SRC)} alt="Luma" style={{ height: 30, width: 'auto' }} draggable={false} />
          <div style={headRight}>
            <button type="button" style={searchBtn} aria-label="Search" onClick={openSearch} data-testid="search-button">
              <SearchIcon size={20} />
            </button>
            {menu(40)}
          </div>
        </header>
        <div style={{ height: 'calc(env(safe-area-inset-top) + 56px)' }} aria-hidden="true" />
        <main style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 32px)' }}>
          {homeView(data, failed || slow, () => setAttempt(a => a + 1), openCard)}
        </main>
      </div>
    </div>
  );
}

export const SKELETON_TIMEOUT_MS = 8000;

/** v228 R3 rest (PM): Recently added under the band: the Plex libraries' recently-added rails (not Trakt or
 * the watchlist), interleaved one from each in turn, 12 at most. */
export const RECENT_MAX = 12;
export function recentAdded(data: HomeData | null): Card[] {
  if (!data || data.plex === false) return [];
  const rails = (Array.isArray(data.rails) ? data.rails : []).filter(r => r && Array.isArray(r.items) && r.id !== 'watchlist' && !String(r.id).startsWith('trakt_'));
  const out: Card[] = [];
  for (let i = 0; out.length < RECENT_MAX && rails.some(r => i < r.items.length); i++) {
    for (const r of rails) { const c = r.items[i]; if (c && out.length < RECENT_MAX) out.push(c); }
  }
  return out;
}

/** Opens main's movie or TV popup; episodes and seasons open their show. */
export function openDetail(modals: { openMovie: (o: object) => void; openTv: (o: object) => void }, c: Card) {
  if (c.tmdb_id) {
    return c.type === 'movie'
      ? modals.openMovie({ tmdbId: c.tmdb_id, origin: 'trakt_movies' })
      : modals.openTv({ tmdbId: c.tmdb_id, origin: 'trakt_shows' });
  }
  const key = Number(c.key);
  if (c.type === 'movie') return modals.openMovie({ ratingKey: key, origin: 'continue_watching' });
  const show = Number(c.show_key || c.key);
  if (c.type === 'episode' && c.season && c.episode) {
    return modals.openTv({ ratingKey: show, origin: 'continue_watching',
      episodeContext: { seasonNumber: c.season, episodeNumber: c.episode, episodeRatingKey: key } });
  }
  modals.openTv({ ratingKey: show, origin: 'recently_added_tv' });
}

function homeView(data: HomeData | null, down: boolean, retry: () => void, onOpen: (c: Card) => void) {
  if (data && data.plex !== false) {
    const any = data.continue_watching.length > 0 || data.rails.some(r => r.items.length > 0);
    if (any) return <HomeRails cw={data.continue_watching} rails={data.rails} onOpen={onOpen} />;
    return <div style={emptyBox} data-testid="home-empty"><p style={emptyLine}>Nothing here yet</p></div>;
  }
  if (data || down) {
    return (
      <div style={emptyBox} role="alert" data-testid="home-down">
        <p style={emptyLine}>Can't reach Plex</p>
        <button type="button" onClick={retry} style={retryBtn}>Retry</button>
      </div>
    );
  }
  return <SkeletonRails />;
}

// Centered in the space under the header: one muted line, optional Retry pill.
const emptyBox: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16,
  minHeight: 'calc(100dvh - 160px)', padding: '0 20px',
};
const emptyLine: React.CSSProperties = { margin: 0, color: 'rgba(255,255,255,0.6)', fontSize: 15 };
const retryBtn: React.CSSProperties = {
  padding: '10px 22px', borderRadius: 999, border: 0, background: LUMA_BLUE,
  color: '#fff', font: 'inherit', fontWeight: 600, cursor: 'pointer',
};

export const homePage: React.CSSProperties = {
  minHeight: '100dvh', color: '#fff', fontFamily: FONT, WebkitFontSmoothing: 'antialiased',
  background: NAVY_BG, backgroundAttachment: 'fixed',
};
/** v213 (Designer): the home page behind the rails is prod 1b4ea74's flat navy, no glow (settings keep homePage's). */
const page: React.CSSProperties = { ...homePage, background: 'var(--color-kiosk-black)', backgroundAttachment: undefined };
export const homeBar: React.CSSProperties = {
  position: 'sticky', top: 0, zIndex: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  padding: 'calc(env(safe-area-inset-top) + 12px) 20px 12px',
  background: 'rgba(21,16,42,0.55)', backdropFilter: 'blur(20px) saturate(160%)', WebkitBackdropFilter: 'blur(20px) saturate(160%)',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
};
/** Phone header: 56px + safe area; logo left, avatar right. */
export const phoneBar: React.CSSProperties = {
  ...homeBar, height: 'calc(env(safe-area-inset-top) + 56px)', boxSizing: 'border-box',
  padding: 'env(safe-area-inset-top) 12px 0 16px',
  // Pinned while scrolling; navy solid at the top fading out, no line or shadow.
  position: 'fixed', top: 0, left: 0, right: 0, zIndex: 20,
  background: 'linear-gradient(to bottom, var(--color-kiosk-black) 0%, var(--color-kiosk-black) 35%, transparent 100%)',
  backdropFilter: 'none', WebkitBackdropFilter: 'none', borderBottom: 'none', boxShadow: 'none',
};
/** Web kiosk header: overlays the slideshow, top gradient, no glass. */
export const kioskBar: React.CSSProperties = {
  position: 'fixed', top: 0, left: 0, right: 0, zIndex: 50, display: 'flex', alignItems: 'center',
  justifyContent: 'space-between', padding: '20px 60px 20px 44px',
  // v245: no top scrim or shadow (closed or open)
};
const headRight: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12 };
/** 44px circular search button, left of the avatar. */
const searchBtn: React.CSSProperties = {
  width: 44, height: 44, borderRadius: 22, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  background: 'rgba(255,255,255,0.10)', border: '1px solid rgba(255,255,255,0.12)', color: '#fff', cursor: 'pointer',
  backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)', padding: 0,
};
export const iconBtn: React.CSSProperties = {
  width: 40, height: 40, borderRadius: 20, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', cursor: 'pointer',
};
