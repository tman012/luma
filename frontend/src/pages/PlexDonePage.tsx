/**
 * Plex forwards here after sign-in. A tab Luma opened closes itself (the
 * original tab is still polling); otherwise (iOS, installed PWA, same
 * window) it returns to /start, which resumes from /api/start/status.
 * It never starts a new Plex PIN.
 */
import { useEffect } from 'react';
import { useNavigate } from 'react-router';
import { StartShell, Center, Logo } from '@/components/start/StartUI';

export function PlexDonePage() {
  const navigate = useNavigate();
  useEffect(() => {
    try { window.close(); } catch { /* not closable */ }
    const t = window.setTimeout(() => navigate('/start', { replace: true }), 400);
    return () => window.clearTimeout(t);
  }, [navigate]);
  return (
    <StartShell step="plex-done">
      <Center key="plex-done">
        <Logo />
      </Center>
    </StartShell>
  );
}
