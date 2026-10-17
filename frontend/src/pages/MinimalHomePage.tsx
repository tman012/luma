/** Main screen of a LUMA_MINIMAL build: the phone-first Home (#11). */
import { useEffect, useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { FONT, LOGO_SRC } from '@/components/start/StartUI';
import { assetUrl } from '@/urlHelpers';
import { HomeRails, SkeletonRails, railCss } from '@/components/home/Rails';
import { fetchHome, type HomeData } from '@/components/home/homeApi';

export function MinimalHomePage() {
  const { authenticated, loading, needsStart, startInProgress, pinUnlock, user, implicit, logout } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [data, setData] = useState<HomeData | null>(null);
  const [failed, setFailed] = useState(false);

  const ready = !loading && authenticated && !startInProgress;
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    // One request returns every rail, so all of them appear together.
    fetchHome().then(d => { if (alive) setData(d); }).catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [ready]);

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
        <nav style={{ display: 'flex', gap: 8 }}>
          {user?.is_admin && (
            <Link to="/settings/libraries" aria-label="Libraries" style={iconBtn}><LibrariesIcon /></Link>
          )}
          {!implicit && (
            <button type="button" aria-label="Sign out" onClick={signOut} disabled={busy} style={iconBtn}><SignOutIcon /></button>
          )}
        </nav>
      </header>
      <main style={{ paddingBottom: 'calc(env(safe-area-inset-bottom) + 32px)' }}>
        {data ? <HomeRails cw={data.continue_watching} rails={data.rails} /> : failed ? null : <SkeletonRails />}
      </main>
    </div>
  );
}

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
  background: 'radial-gradient(ellipse at 50% 0%, #262a66 0%, #15102a 55%, #0a0818 100%)', backgroundAttachment: 'fixed',
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
