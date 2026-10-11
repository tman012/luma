/**
 * UnlockPage — PIN sign-in for a single-account install (set up with the
 * Start flow) when Settings → Security requires a PIN for this address.
 */
import { useState } from 'react';
import { Navigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { StartShell, Center, ErrorLine, Logo, PinPad } from '@/components/start/StartUI';

export function UnlockPage() {
  const { authenticated, loading, needsStart, refreshUser } = useAuth();
  const [pin, setPin] = useState('');
  const [error, setError] = useState('');
  const [shake, setShake] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!loading && needsStart) return <Navigate to="/start" replace />;
  if (!loading && authenticated) return <Navigate to="/" replace />;

  const submit = async (value: string) => {
    setBusy(true);
    let msg = 'Wrong PIN';
    try {
      const res = await fetch('/api/auth/unlock', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin: value }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        await refreshUser();
        return;
      }
      msg = data.error || msg;
    } catch {
      msg = "Couldn't reach Luma";
    } finally {
      setBusy(false);
    }
    setShake(true);
    window.setTimeout(() => { setShake(false); setPin(''); setError(msg); }, 450);
  };

  return (
    <StartShell step="unlock">
      <Center>
        <Logo top="10vh" />
        <div style={{ minHeight: 20, marginTop: 18 }}>{error && <ErrorLine text={error} />}</div>
        <PinPad value={pin} onChange={v => { setError(''); setPin(v); }} onComplete={submit} shake={shake} disabled={busy} />
      </Center>
    </StartShell>
  );
}
