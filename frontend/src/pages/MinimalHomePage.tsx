/** Main screen of a LUMA_MINIMAL build: the phone-first Home (#11). */
import { useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { FONT, LOGO_SRC, NAVY_BG } from '@/components/start/StartUI';
import { assetUrl } from '@/urlHelpers';
import { HomeRails, SkeletonRails, WYM_RAIL_ID, cardIds, railCss } from '@/components/home/Rails';
import { img } from '@/components/home/homeApi';
import { openWhenReady } from '@/services/modalPrefetch';
import { useModalHost } from '@/contexts/ModalHostContext';
import type { Card } from '@/components/home/homeApi';
import { LUMA_BLUE } from '@/ui/lumaButton';
import { dismissMissing, fetchHome, type HomeData } from '@/components/home/homeApi';
import { DismissMissingSheet } from '@/features/home/DismissMissingSheet';
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
  // v254 What you're missing: prod's long-press dismiss (MobileHomePage confirmDismissMissing).
  const [dismissTarget, setDismissTarget] = useState<Card | null>(null);
  const [wymOverride, setWymOverride] = useState<Card[] | null>(null);
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
    setWymOverride(null);
    // v251: the skeleton stays until every rail is in, then they all swap in at once. One request returns
    // every ready rail (the server drops a failed or slow one at 6 s); past SKELETON_TIMEOUT_MS the skeleton
    // gives way to Retry and a late answer is dropped rather than popped in.
    // v251: then it holds until the first on-screen posters of every rail have decoded (POSTER_HOLD_MS cap,
    // never past SKELETON_TIMEOUT_MS), so they don't fill in after the swap.
    let done = false;
    const t0 = Date.now();
    const timer = window.setTimeout(() => { if (alive && !done) { done = true; setSlow(true); } }, SKELETON_TIMEOUT_MS);
    fetchHome()
      .then(d => {
        if (!alive || done) return;
        done = true;
        window.clearTimeout(timer);
        const cap = Math.max(0, Math.min(POSTER_HOLD_MS, SKELETON_TIMEOUT_MS - (Date.now() - t0)));
        void decodeAll(firstPosterUrls(d, window.innerWidth), cap).then(() => { if (alive) setData(d); });
      })
      .catch(() => { if (alive && !done) { done = true; setFailed(true); } });
    return () => { alive = false; window.clearTimeout(timer); };
  }, [ready, attempt, kiosk]);

  if (loading) return null;
  if (!authenticated) return <Navigate to={needsStart ? '/start' : pinUnlock ? '/unlock' : '/login'} replace />;
  if (startInProgress) return <Navigate to="/start" replace />;

  const confirmDismiss = async () => {
    const card = dismissTarget;
    setDismissTarget(null);
    if (!card?.tmdb_id) return;
    const tid = card.tmdb_id;
    const base = wymOverride ?? data?.rails.find(r => r.id === WYM_RAIL_ID)?.items ?? [];
    setWymOverride(base.filter(c => c.tmdb_id !== tid));
    try {
      const result = await dismissMissing(tid);
      if (result.ok) setWymOverride(result.items);
    } catch {
      // Keep the optimistic removal.
    }
  };

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
          {homeView(withMissing(data, wymOverride), failed || slow, () => setAttempt(a => a + 1), openCard, setDismissTarget)}
        </main>
      </div>
      {dismissTarget && (
        <DismissMissingSheet
          item={{ title: dismissTarget.title, year: dismissTarget.subtitle || null }}
          onConfirm={() => { void confirmDismiss(); }}
          onClose={() => setDismissTarget(null)}
        />
      )}
    </div>
  );
}

/** v251: the server's 6 s rail budget (app/home_rails.HOME_DEADLINE_S) plus 1 s to reach the phone. */
export const SKELETON_TIMEOUT_MS = 7000;

/** v251: after /api/home answers, the skeleton waits at most this long for the first posters to decode. */
export const POSTER_HOLD_MS = 1500;

/** How many cards of a rail fit the first screen width (Rails.tsx sizes: padding, clamp() widths, 12px gap). */
function fits(vw: number, card: number, pad: number): number {
  return Math.max(1, Math.ceil((vw - pad) / (card + 12)));
}

/** v251: the image URLs the first on-screen tiles of every rail will show, exactly as the tiles build them
 * (img(art || poster) for Continue watching, img(poster) otherwise), so the decoded copies are the ones used. */
export function firstPosterUrls(data: HomeData | null, vw: number): string[] {
  if (!data || data.plex === false) return [];
  const clamp = (lo: number, v: number, hi: number) => Math.min(hi, Math.max(lo, v));
  const urls: string[] = [];
  const add = (cards: Card[], n: number, wide: boolean) => {
    for (const c of cards.slice(0, n)) {
      const u = img(wide ? (c.art || c.poster) : c.poster);
      if (u && !urls.includes(u)) urls.push(u);
    }
  };
  const { cw, rails } = homeParts(data);
  add(cw, fits(vw, clamp(240, vw * 0.72, 320), 20), true);
  for (const r of rails) {
    add(r.items, r.id === 'watchlist' ? fits(vw, 116, 12) : fits(vw, clamp(112, vw * 0.3, 160), 20), false);
  }
  return urls;
}

/** Load and decode each URL (an error counts as done); resolves when all are done or after *capMs*. */
export function decodeAll(urls: string[], capMs: number): Promise<void> {
  if (!urls.length || typeof Image === 'undefined') return Promise.resolve();
  const each = urls.map(u => {
    const im = new Image();
    im.decoding = 'async';
    im.src = u;
    return typeof im.decode === 'function' ? im.decode().catch(() => {}) : Promise.resolve();
  });
  return new Promise<void>(resolve => {
    const t = window.setTimeout(resolve, capMs);
    void Promise.all(each).then(() => { window.clearTimeout(t); resolve(); });
  });
}

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

/** The parts of a /api/home answer Home renders, tolerating {} or a missing / malformed field. */
type HomeParts = { cw: HomeData['continue_watching']; rails: HomeData['rails'] };
export function homeParts(data: Partial<HomeData> | null | undefined): HomeParts {
  const cw = Array.isArray(data?.continue_watching) ? data!.continue_watching.filter(Boolean) : [];
  const rails = Array.isArray(data?.rails) ? data!.rails.filter(r => r && Array.isArray(r.items)) : [];
  return { cw, rails };
}

/** v254: the What you're missing rail after a dismiss (the optimistic removal, then the server's top-up). */
export function withMissing(data: HomeData | null, items: Card[] | null): HomeData | null {
  if (!data || !items || !Array.isArray(data.rails)) return data;
  return { ...data, rails: data.rails.map(r => (r && r.id === WYM_RAIL_ID ? { ...r, items } : r)) };
}

function homeView(data: HomeData | null, down: boolean, retry: () => void, onOpen: (c: Card) => void,
                  onDismiss: (c: Card) => void) {
  if (data && data.plex !== false) {
    // Tester LOW: /api/home answering {} (or without rails / continue_watching) shows the empty state, not a crash
    const { cw, rails } = homeParts(data);
    const any = cw.length > 0 || rails.some(r => r.items.length > 0);
    if (any) return <HomeRails cw={cw} rails={rails} onOpen={onOpen} onDismiss={onDismiss} />;
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
