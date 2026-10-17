/** Main screen of a LUMA_MINIMAL build: the phone-first Home (#11). */
import { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { FONT, LOGO_SRC, NAVY_BG } from '@/components/start/StartUI';
import { assetUrl } from '@/urlHelpers';
import { HomeRails, SkeletonRails, railCss } from '@/components/home/Rails';
import { useModalHost } from '@/contexts/ModalHostContext';
import type { Card } from '@/components/home/homeApi';
import { LUMA_BLUE } from '@/ui/lumaButton';
import { fetchHome, type HomeData } from '@/components/home/homeApi';
import { KioskHome, useKioskWidth } from '@/components/home/KioskHome';

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
  const openCard = (c: Card) => openDetail(modals, c);

  const ready = !loading && authenticated && !startInProgress;
  useEffect(() => {
    if (!ready || kiosk) return;
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

  const menu = (user?.is_admin || !implicit) && (
    <AccountMenu name={user?.name || ''} admin={!!user?.is_admin} canSignOut={!implicit} busy={busy} onSignOut={signOut} />
  );

  if (kiosk) {
    return (
      <div data-step="home" data-layout="kiosk">
        <KioskHome />
        <header style={kioskBar} data-testid="kiosk-header">
          <img src={assetUrl(LOGO_SRC)} alt="Luma" style={{ height: 28, width: 'auto' }} draggable={false} />
          {menu}
        </header>
      </div>
    );
  }

  return (
    <div style={page} data-step="home">
      <style>{railCss}</style>
      <header style={bar}>
        <img src={assetUrl(LOGO_SRC)} alt="Luma" style={{ height: 30, width: 'auto' }} draggable={false} />
        {menu}
      </header>
      <main style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 32px)' }}>
        {homeView(data, failed || slow, () => setAttempt(a => a + 1), openCard)}
      </main>
    </div>
  );
}

export const SKELETON_TIMEOUT_MS = 8000;

/** Opens main's movie or TV popup; episodes and seasons open their show. */
export function openDetail(modals: { openMovie: (o: object) => void; openTv: (o: object) => void }, c: Card) {
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

/** Avatar button with a small menu: Libraries (admins) and Sign out. */
function AccountMenu({ name, admin, canSignOut, busy, onSignOut }: {
  name: string; admin: boolean; canSignOut: boolean; busy: boolean; onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', close);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  const initial = (name.trim()[0] || '').toUpperCase();
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button type="button" aria-label="Account" aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen(o => !o)} style={iconBtn}>
        {initial ? <span style={{ fontSize: 16, fontWeight: 600 }}>{initial}</span> : <PersonIcon />}
      </button>
      {open && (
        <div role="menu" style={menu}>
          {admin && (
            <Link role="menuitem" to="/settings/libraries" style={menuItem}><LibrariesIcon /> Libraries</Link>
          )}
          {canSignOut && (
            <button role="menuitem" type="button" onClick={onSignOut} disabled={busy} style={menuItem}><SignOutIcon /> Sign out</button>
          )}
        </div>
      )}
    </div>
  );
}

function PersonIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="8" r="4" /><path d="M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6" />
    </svg>
  );
}

const menu: React.CSSProperties = {
  position: 'absolute', right: 0, top: 48, minWidth: 180, padding: 6, borderRadius: 14,
  background: 'rgba(21,16,42,0.96)', border: '1px solid rgba(255,255,255,0.1)',
  boxShadow: '0 12px 32px rgba(0,0,0,0.45)', backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
};
const menuItem: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: '12px 12px', borderRadius: 10,
  background: 'transparent', border: 'none', color: '#fff', font: 'inherit', fontSize: 15,
  textDecoration: 'none', cursor: 'pointer', textAlign: 'left',
};
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

function LibrariesIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
      <path d="M4 6h16M4 12h16M4 18h10" />
    </svg>
  );
}
function SignOutIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l5-5-5-5M15 12H4" />
    </svg>
  );
}

export const homePage: React.CSSProperties = {
  minHeight: '100dvh', color: '#fff', fontFamily: FONT, WebkitFontSmoothing: 'antialiased',
  background: NAVY_BG, backgroundAttachment: 'fixed',
};
const page = homePage;
export const homeBar: React.CSSProperties = {
  position: 'sticky', top: 0, zIndex: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  padding: 'calc(env(safe-area-inset-top) + 12px) 20px 12px',
  background: 'rgba(21,16,42,0.55)', backdropFilter: 'blur(20px) saturate(160%)', WebkitBackdropFilter: 'blur(20px) saturate(160%)',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
};
const bar = homeBar;
/** Web kiosk header: overlays the slideshow, top gradient, no glass. */
export const kioskBar: React.CSSProperties = {
  position: 'fixed', top: 0, left: 0, right: 0, zIndex: 50, display: 'flex', alignItems: 'center',
  justifyContent: 'space-between', padding: '24px 60px',
  background: 'linear-gradient(to bottom, rgba(0,0,0,0.8), transparent)',
};
export const iconBtn: React.CSSProperties = {
  width: 40, height: 40, borderRadius: 20, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', cursor: 'pointer',
};
