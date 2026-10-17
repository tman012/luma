import { apiFetch } from '../apiClient';
/**
 * StartPage — first-run setup for a fresh install.
 *
 * Welcome (Continue with Plex) → (pick a server when there are several) →
 * Create account → (Access, full build only) → Home, with a one-time toast.
 * Only reachable while the install has no users (the backend refuses every
 * Plex step once an account exists) and never shown again once finished.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Navigate, useNavigate } from 'react-router';
import { assetUrl } from '@/urlHelpers';
import { markSetupJustFinished } from '@/components/start/SetupToast';
import { useAuth } from '@/contexts/AuthContext';
import { isMinimal } from '@/minimal';
import { openPlexPopup, plexAuthMode } from '@/utils/plexAuth';
import {
  StartShell, Center, Bottom, Title, ErrorLine, Status, Logo, LogoGap, PlexMark, PinPad,
  primaryBtn, accentBtn, plexBtn, linkBtn, list, row, FONT, fieldInput,
} from '@/components/start/StartUI';

type Step = 'loading' | 'welcome' | 'servers' | 'connecting' | 'password' | 'access';
export type AuthRequired = 'disabled_local' | 'enabled';
type Mode = AuthRequired;

interface Server { machine_id: string; name: string }

export const ACCESS_CHOICES: Array<{ value: Mode; label: string }> = [
  { value: 'disabled_local', label: 'Disabled for local addresses' },
  { value: 'enabled', label: 'Enabled' },
];

async function api<T = Record<string, unknown>>(url: string, init?: RequestInit): Promise<{ ok: boolean; status: number; data: T }> {
  try {
    const res = await apiFetch(url, { credentials: 'same-origin', ...init });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data: data as T };
  } catch {
    return { ok: false, status: 0, data: { error: "Couldn't reach Luma" } as T };
  }
}

const sendJson = (url: string, body?: unknown, method = 'POST') =>
  api<{ error?: string; restart?: boolean; restarting?: boolean; auth_url?: string; server?: string }>(url, {
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}),
  });

export function StartPage() {
  const { refreshUser, authenticated, startInProgress, loading } = useAuth();
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>('loading');
  const [error, setError] = useState('');
  const [authUrl, setAuthUrl] = useState('');
  const [servers, setServers] = useState<Server[]>([]);
  const [serverName, setServerName] = useState('');
  const finishRef = useRef<() => void>(() => {});
  const [busy, setBusy] = useState(false);
  const [passwordSet, setPasswordSet] = useState(false);
  const pollRef = useRef<number | null>(null);
  const popupRef = useRef<Window | null>(null);
  const pollFnRef = useRef<() => void>(() => {});

  // Best effort: the Plex pop-up has no return page, so the opener closes it.
  const closePopup = () => {
    try { popupRef.current?.close(); } catch { /* cross-origin or gone */ }
    popupRef.current = null;
  };

  const stopPolling = () => {
    if (pollRef.current !== null) window.clearTimeout(pollRef.current);
    pollRef.current = null;
  };

  // Resume where this browser left off (e.g. a reload mid-setup).
  useEffect(() => {
    let alive = true;
    api<{ needs_start: boolean; in_progress: boolean; server?: string; password_set?: boolean; step?: string }>('/api/start/status').then(({ data }) => {
      if (!alive) return;
      if (data.needs_start && data.step === 'plex') {
        // Back from Plex (same window): keep polling the PIN this browser started.
        setStep('welcome');
        setAuthUrl('pending');
        pollFnRef.current();
        return;
      }
      if (data.in_progress) {
        setServerName(data.server || '');
        setPasswordSet(!!data.password_set);
        if (!data.password_set) setStep('password');
        else if (isMinimal()) finishRef.current();
        else setStep('access');
      }
      else if (!data.needs_start) navigate('/', { replace: true });
      else setStep('welcome');
    });
    return () => { alive = false; stopPolling(); closePopup(); };
  }, [navigate]);

  const chooseServer = useCallback(async (s: Server, count: number) => {
    setError('');
    setServerName(s.name);
    setStep('connecting');
    const { ok, data } = await sendJson('/api/start/plex/server', { machine_id: s.machine_id });
    if (ok) { setStep('password'); return; }
    setStep(count > 1 && !data.restart ? 'servers' : 'welcome');
    if (!data.restart) setAuthUrl('');
    setError(data.error || `Couldn't connect to ${s.name}`);
  }, []);

  const poll = useCallback(() => {
    stopPolling();
    const tick = async () => {
      const { ok, data } = await api<{ status?: string; servers?: Server[]; restart?: boolean }>('/api/start/plex/pin');
      if (!ok) {
        if (data.restart) { closePopup(); setStep('welcome'); setAuthUrl(''); return; }
        pollRef.current = window.setTimeout(tick, 3000);
        return;
      }
      if (data.status === 'waiting') { pollRef.current = window.setTimeout(tick, 2000); return; }
      if (data.status === 'expired') { closePopup(); setError('Sign-in timed out. Try again.'); setAuthUrl(''); return; }
      closePopup();
      const found = data.servers || [];
      setServers(found);
      if (found.length === 0) { setError('No Plex servers found on this account.'); setAuthUrl(''); }
      else if (found.length === 1) chooseServer(found[0]!, 1);
      else setStep('servers');
    };
    pollRef.current = window.setTimeout(tick, 1500);
  }, [chooseServer]);

  pollFnRef.current = poll;

  const signInWithPlex = async () => {
    setError('');
    stopPolling();
    setBusy(true);
    // Open the pop-up inside the click so blockers allow it, then point it at Plex.
    const popup = openPlexPopup();
    const mode = plexAuthMode(popup);
    const { ok, data } = await sendJson('/api/start/plex/pin', { 'mode': mode });
    setBusy(false);
    if (!ok || !data.auth_url) {
      popup?.close();
      popupRef.current = null;
      setError(data.error || "Couldn't reach Plex. Try again.");
      return;
    }
    setAuthUrl(data.auth_url);
    if (popup) {
      popupRef.current = popup;
      popup.location.href = data.auth_url;
      poll();
    } else {
      // Blocked pop-up or installed app: go to Plex in this tab; Plex sends
      // the browser back to /start, which resumes polling.
      window.location.href = data.auth_url;
    }
  };

  const finish = async () => {
    setBusy(true);
    const { data } = await sendJson('/api/start/finish');
    if (data.restarting) {
      // Luma restarts to load the new setup; wait for it to come back.
      await new Promise(r => setTimeout(r, 1500));
      for (let i = 0; i < 40; i++) {
        if ((await api('/health')).ok) break;
        await new Promise(r => setTimeout(r, 750));
      }
    }
    await refreshUser();
    markSetupJustFinished();
    window.location.replace(assetUrl('/'));
  };
  finishRef.current = finish;

  if (!loading && authenticated && !startInProgress && step === 'welcome') {
    return <Navigate to="/" replace />;
  }

  return (
    <StartShell step={step}>
      {step === 'welcome' && (
        <Center key="welcome">
          <Logo />
          <Bottom>
            {error && <ErrorLine text={error} />}
            {authUrl && !error ? (
              <>
                <Status text="Waiting for Plex…" />
                {authUrl !== 'pending' && <a href={authUrl} target="_blank" rel="noreferrer" style={linkBtn}>Open Plex again</a>}
                <button style={linkBtn} onClick={() => { stopPolling(); closePopup(); setAuthUrl(''); setError(''); }}>Cancel</button>
              </>
            ) : (
              <button className="luma-start-btn" style={plexBtn} onClick={signInWithPlex} disabled={busy}>
                <PlexMark /> Continue with Plex
              </button>
            )}
          </Bottom>
        </Center>
      )}

      {step === 'servers' && (
        <Center key="servers">
          <Title>Choose a server</Title>
          <div style={list}>
            {servers.map(s => (
              <button key={s.machine_id} className="luma-start-row" style={row} onClick={() => chooseServer(s, servers.length)}>
                <span>{s.name}</span>
                <span style={{ opacity: 0.4 }}>›</span>
              </button>
            ))}
          </div>
          <Bottom>{error && <ErrorLine text={error} />}</Bottom>
        </Center>
      )}

      {step === 'connecting' && (
        <Center key="connecting">
          <Title>{serverName || 'Plex'}</Title>
          <Bottom><Status text="Connecting…" /></Bottom>
        </Center>
      )}

      {step === 'password' && (
        <Center key="password">
          <AccountStep
            onSaved={() => { setPasswordSet(true); if (isMinimal()) finish(); else setStep('access'); }}
            onRestart={() => setStep('welcome')}
            locked={busy}
          />
        </Center>
      )}

      {step === 'access' && (
        <Center key="access">
          <AccessChooser onSaved={() => finish()} passwordSet={passwordSet} />
        </Center>
      )}

    </StartShell>
  );
}

/** Create account: the owner's username and password for /login. */
export function AccountStep({ onSaved, onRestart, locked = false }: {
  onSaved: () => void; onRestart: () => void; locked?: boolean;
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const busy = saving || locked;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setSaving(true);
    const { ok, data } = await sendJson('/api/start/account', { username, password });
    setSaving(false);
    if (ok) { onSaved(); return; }
    if (data.restart) { onRestart(); return; }
    setError(data.error || 'Something went wrong');
  };

  return (
    <form id="start-account" method="post" action="#" onSubmit={submit} style={pwForm}>
      <Logo />
      <LogoGap />
      <Title>Create account</Title>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, width: '100%', marginTop: 28 }}>
        <label style={pwLabel}>
          Username
          <input
            id="username" name="username" type="text" autoComplete="username" autoCapitalize="none"
            spellCheck={false} value={username} onChange={e => { setError(''); setUsername(e.target.value); }}
            style={pwInput} disabled={busy} maxLength={64} autoFocus
          />
        </label>
        <label style={pwLabel}>
          Password
          <input
            id="new-password" name="new-password" type="password" autoComplete="new-password"
            value={password} onChange={e => { setError(''); setPassword(e.target.value); }}
            style={pwInput} disabled={busy} maxLength={256}
          />
        </label>
      </div>
      <Bottom>
        {error && <ErrorLine text={error} />}
        <button type="submit" className="luma-start-btn" style={accentBtn} disabled={busy || !username.trim() || !password}>
          Create account
        </button>
      </Bottom>
    </form>
  );
}

const pwLabel: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', gap: 6, width: '100%', fontSize: 13, fontWeight: 500,
  color: 'rgba(255,255,255,0.7)', textAlign: 'left',
};
const pwForm: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', alignItems: 'center', width: '100%', maxWidth: 360,
};
const pwInput: React.CSSProperties = fieldInput;

/** "Require PIN" segmented choice with an inline keypad (entered twice). */
export function AccessChooser({ onSaved, initial = 'disabled_local', pinSet = false, passwordSet = false }: {
  onSaved: (mode: Mode) => void;
  initial?: Mode;
  pinSet?: boolean;
  compactTitle?: boolean;
  /** The owner set a password: choose the mode only, no PIN keypad. */
  passwordSet?: boolean;
}) {
  const [mode, setMode] = useState<Mode>(initial);
  const [pin, setPin] = useState('');
  const [first, setFirst] = useState('');
  const [shake, setShake] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const save = async (p?: string, c?: string) => {
    setBusy(true);
    const { ok, data } = await sendJson('/api/security', p ? { auth_required: mode, pin: p, confirm: c } : { auth_required: mode }, 'PUT');
    setBusy(false);
    if (ok) { onSaved(mode); return; }
    setPin(''); setFirst('');
    setError(data.error || 'Something went wrong');
  };

  const complete = (value: string) => {
    if (!first) { setFirst(value); setPin(''); return; }
    if (value !== first) {
      setShake(true);
      window.setTimeout(() => { setShake(false); setPin(''); setFirst(''); setError("PINs didn't match"); }, 450);
      return;
    }
    save(first, value);
  };

  // Remote visitors always sign in, so a PIN is needed in both modes,
  // unless the owner already set a password.
  const needsPin = !passwordSet;
  const heading = passwordSet ? 'Require sign-in' : 'Require PIN';
  return (
    <>
      <h1 style={{ margin: 0, marginTop: 0, fontSize: 28, fontWeight: 600, textAlign: 'center' }}>
        {heading}
      </h1>
      <div role="radiogroup" aria-label={heading} style={seg}>
        {ACCESS_CHOICES.map(c => (
          <button
            key={c.value}
            type="button"
            role="radio"
            aria-checked={mode === c.value}
            onClick={() => { setMode(c.value); setPin(''); setFirst(''); setError(''); }}
            style={{ ...segItem, ...(mode === c.value ? segOn : null), flex: c.value === 'disabled_local' ? 2.2 : 1 }}
          >
            {c.label}
          </button>
        ))}
      </div>
      {needsPin ? (
        <>
          <p style={{ margin: '26px 0 0', fontSize: 17, fontWeight: 500, color: 'rgba(255,255,255,0.9)' }}>
            {first ? 'Confirm PIN' : pinSet ? 'New PIN' : 'Create a PIN'}
          </p>
          <div style={{ minHeight: 20, marginTop: 8 }}>{error && <ErrorLine text={error} />}</div>
          <PinPad value={pin} onChange={v => { setError(''); setPin(v); }} onComplete={complete} shake={shake} disabled={busy} compact />
          {pinSet && !first && (
            <button style={{ ...linkBtn, marginTop: -24, marginBottom: 24 }} onClick={() => save()} disabled={busy}>Keep current PIN</button>
          )}
        </>
      ) : (
        <div style={{ width: '100%', maxWidth: 360, marginTop: 32, display: 'flex', flexDirection: 'column', gap: 16, alignItems: 'center' }}>
          {error && <ErrorLine text={error} />}
          <button className="luma-start-btn" style={primaryBtn} onClick={() => save()} disabled={busy}>Continue</button>
        </div>
      )}
    </>
  );
}

const seg: React.CSSProperties = {
  display: 'flex', width: '100%', maxWidth: 440, marginTop: 22, padding: 3, gap: 2, borderRadius: 11,
  background: 'rgba(255,255,255,0.1)', boxSizing: 'border-box',
};
const segItem: React.CSSProperties = {
  border: 'none', borderRadius: 8, padding: '8px 6px', minHeight: 34, background: 'transparent',
  color: 'rgba(255,255,255,0.85)', fontSize: 13, fontWeight: 500, fontFamily: FONT, cursor: 'pointer', lineHeight: 1.2,
};
const segOn: React.CSSProperties = { background: 'rgba(255,255,255,0.92)', color: '#15102a', fontWeight: 600, boxShadow: '0 1px 4px rgba(0,0,0,0.25)' };
