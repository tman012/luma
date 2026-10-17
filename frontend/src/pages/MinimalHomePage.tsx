/** Main screen of a LUMA_MINIMAL build: the phone-first Home (#11). */
import { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { FONT, LOGO_SRC, NAVY_BG } from '@/components/start/StartUI';
import { assetUrl } from '@/urlHelpers';
import { HomeRails, SkeletonRails, railCss } from '@/components/home/Rails';
import { fetchHome, type HomeData } from '@/components/home/homeApi';

export function MinimalHomePage() {
  const { authenticated, loading, needsStart, startInProgress, pinUnlock, user, implicit, logout } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [data, setData] = useState<HomeData | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const ready = !loading && authenticated && !startInProgress;
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    setFailed(false);
    // One request returns every rail, so all of them appear together.
    fetchHome().then(d => { if (alive) setData(d); }).catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [ready, attempt]);

  if (loading) return null;
  if (!authenticated) return <Navigate to={needsStart ? '/start' : pinUnlock ? '/unlock' : '/login'} replace />;
  if (startInProgress) return <Navigate to="/start" replace />;

  const signOut = async () => {
    setBusy(true);
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <div style={page} data-step="home">
      <style>{railCss}</style>
      <header style={bar}>
        <img src={assetUrl(LOGO_SRC)} alt="Luma" style={{ height: 30, width: 'auto' }} draggable={false} />
        {(user?.is_admin || !implicit) && (
          <AccountMenu name={user?.name || ''} admin={!!user?.is_admin} canSignOut={!implicit} busy={busy} onSignOut={signOut} />
        )}
      </header>
      <main style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 32px)' }}>
        {data ? <HomeRails cw={data.continue_watching} rails={data.rails} />
          : failed ? (
            <div style={failBox} role="alert">
              <p style={{ margin: 0 }}>Couldn't load Home</p>
              <button type="button" onClick={() => setAttempt(a => a + 1)} style={retryBtn}>Retry</button>
            </div>
          ) : <SkeletonRails />}
      </main>
    </div>
  );
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
const failBox: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, padding: '96px 20px',
  color: 'rgba(255,255,255,0.75)', fontSize: 15,
};
const retryBtn: React.CSSProperties = {
  padding: '10px 20px', borderRadius: 999, border: '1px solid rgba(255,255,255,0.18)',
  background: 'rgba(255,255,255,0.08)', color: '#fff', font: 'inherit', cursor: 'pointer',
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
export const iconBtn: React.CSSProperties = {
  width: 40, height: 40, borderRadius: 20, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', cursor: 'pointer',
};
