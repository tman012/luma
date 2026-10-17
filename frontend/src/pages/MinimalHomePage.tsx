/** Main screen of a LUMA_MINIMAL build. */
import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { StartShell, Center, Bottom, Sub, Logo, linkBtn } from '@/components/start/StartUI';

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
        {user?.name && <Sub>{user.name}</Sub>}
        <Bottom>
          {!implicit && (
            <button style={linkBtn} onClick={signOut} disabled={busy}>Sign out</button>
          )}
        </Bottom>
      </Center>
    </StartShell>
  );
}
