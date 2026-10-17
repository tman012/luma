/** Main screen of a LUMA_MINIMAL build. */
import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { StartShell, Center, Bottom, Logo, LogoGap, TRIM, primaryBtn } from '@/components/start/StartUI';

export function MinimalHomePage() {
  const { authenticated, loading, needsStart, startInProgress, pinUnlock, user, implicit, logout } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  if (loading) return null;
  if (!authenticated) return <Navigate to={needsStart ? '/start' : pinUnlock ? '/unlock' : '/login'} replace />;
  if (startInProgress) return <Navigate to="/start" replace />;

  const signOut = async () => {
    setBusy(true);
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <StartShell step="home">
      <Center key="home">
        <Logo />
        {user?.name ? (
          <>
            <LogoGap />
            <p style={homeName}>{user.name}</p>
          </>
        ) : null}
        <Bottom>
          {!implicit && (
            <button className="luma-start-btn" style={primaryBtn} onClick={signOut} disabled={busy}>Sign out</button>
          )}
        </Bottom>
      </Center>
    </StartShell>
  );
}

const homeName: React.CSSProperties = { margin: 0, fontSize: 15, lineHeight: 1, color: 'rgba(255,255,255,0.55)', textAlign: 'center', ...TRIM };
