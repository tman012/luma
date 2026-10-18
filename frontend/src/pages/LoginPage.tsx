/**
 * LoginPage — sign-in on Luma's navy backdrop, matching Get Started's
 * Create account step: wordmark, 32px gap, heading, shared fields and the
 * blue primary button.
 * Password fields use type="password" for proper keyboard on mobile.
 */

import { useState, useRef, useEffect } from 'react';
import { StartShell, Logo, LogoGap, Title, primaryBtn, fieldInput, fieldLabel, FONT } from '@/components/start/StartUI';
import { useNavigate, useLocation } from 'react-router';
import { getUrlBase } from '@/urlHelpers';
import { useAuth } from '@/contexts/AuthContext';
import { apiFetch } from '../apiClient';

type Flow = 'signin' | 'signup';
type SignUpStep = 'email' | 'pin' | 'confirm' | 'pending';

export function LoginPage() {
  const { login, authenticated, loading, needsStart, pinUnlock, signupOpen, implicit } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // Sent here by a "Sign in to …" button (an implicit LAN session, or a
  // session the server says expired while this tab still thinks it's signed
  // in): show the form instead of bouncing back. After signing in, go back.
  const signingIn = !!(location.state as { signIn?: boolean } | null)?.signIn;

  const [flow, setFlow] = useState<Flow>(
    (location.state as any)?.flow === 'signup' ? 'signup' : 'signin',
  );
  useEffect(() => {
    if (!loading && !signupOpen && flow === 'signup') setFlow('signin');
  }, [loading, signupOpen, flow]);
  const [signupStep, setSignupStep] = useState<SignUpStep>('email');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [pin, setPin] = useState('');
  const [confirmPin, setConfirmPin] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const emailRef = useRef<HTMLInputElement>(null);
  const pinRef = useRef<HTMLInputElement>(null);
  const confirmPinRef = useRef<HTMLInputElement>(null);
  const regEmailRef = useRef<HTMLInputElement>(null);
  const regPinRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!loading && authenticated && !signingIn) {
      const from = (location.state as any)?.from || nextFromQuery(location.search) || '/';
      navigate(from, { replace: true });
    }
  }, [authenticated, loading, navigate, location, signingIn]);

  // Fresh install → Start flow; single-account install → PIN keypad.
  useEffect(() => {
    if (loading || authenticated) return;
    if (needsStart) navigate('/start', { replace: true });
    else if (pinUnlock) navigate('/unlock', { replace: true });
  }, [needsStart, pinUnlock, authenticated, loading, navigate]);

  useEffect(() => {
    if (flow === 'signin') {
      setTimeout(() => emailRef.current?.focus(), 100);
    }
    if (flow === 'signup') {
      if (signupStep === 'email') setTimeout(() => regEmailRef.current?.focus(), 100);
      if (signupStep === 'pin') setTimeout(() => regPinRef.current?.focus(), 100);
      if (signupStep === 'confirm') setTimeout(() => confirmPinRef.current?.focus(), 100);
    }
  }, [flow, signupStep]);

  function switchToSignUp() {
    setFlow('signup'); setSignupStep('email'); setEmail(''); setPin(''); setConfirmPin(''); setError(''); setBusy(false);
  }
  function switchToSignIn() {
    setFlow('signin'); setName(''); setEmail(''); setPin(''); setError(''); setBusy(false);
  }

  async function handleSignIn(e?: React.FormEvent) {
    e?.preventDefault();
    if (!email.trim() || !pin) return;
    setBusy(true); setError('');
    const result = await login(email.trim(), pin);
    if (result.success && signingIn) {
      navigate((location.state as { from?: string } | null)?.from || '/', { replace: true });
      return;
    }
    if (!result.success) { setError(result.error || INVALID_LOGIN); setPin(''); setBusy(false); setTimeout(() => pinRef.current?.focus(), 0); }
  }

  function handleRegEmailNext(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = email.trim();
    if (!trimmed) return;
    if (!trimmed.includes('@') || !trimmed.split('@')[1]?.includes('.')) {
      setError('Please enter a valid email address'); return;
    }
    setName(trimmed.split('@')[0] || trimmed);
    setPin(''); setConfirmPin(''); setError(''); setSignupStep('pin');
  }
  function handleRegPinNext(e?: React.FormEvent) {
    e?.preventDefault();
    if (pin !== pin.trim()) { setError(PASSWORD_SPACE_ERROR); return; }
    if (pin.length < 4) { setError('Password must be at least 4 characters'); return; }
    setConfirmPin(''); setError(''); setSignupStep('confirm');
  }
  async function handleRegisterSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    if (confirmPin.length < 4) { setError('Please re-enter your password'); return; }
    if (confirmPin !== pin) { setError("Passwords don't match. Try again."); setConfirmPin(''); return; }
    setBusy(true); setError('');
    try {
      const res = await apiFetch('/api/auth/register', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), email: email.trim(), pin }),
      });
      const data = await res.json();
      if (res.ok && data.success) { setSignupStep('pending'); }
      else { setError(data.error || 'Something went wrong.'); setConfirmPin(''); }
    } catch { setError('Network error.'); setConfirmPin(''); }
    finally { setBusy(false); }
  }

  if (loading) {
    return <StartShell step="login"><div /></StartShell>;
  }

  return (
    <StartShell step="login">
      <div style={columnStyle}>
        <Logo />
        <LogoGap />
        <Title>{flow === 'signup' ? 'Create account' : 'Sign in'}</Title>
        <div style={formBlockStyle}>
        {/* SIGN IN */}
        {flow === 'signin' && (
          <>
            <form onSubmit={handleSignIn}>
              <label style={labelStyle} htmlFor="login-username">Email or username</label>
              <input
                id="login-username" name="username"
                ref={emailRef} style={inputStyle} type="text" value={email}
                onChange={(e) => { setEmail(e.target.value); setError(''); }}
                onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }}
                onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.14)'; }}
                autoComplete="username" autoCapitalize="off" spellCheck={false} disabled={busy}
              />
              <label style={{ ...labelStyle, marginTop: 16 }} htmlFor="login-password">Password</label>
              <input
                id="login-password" name="password"
                ref={pinRef} style={inputStyle} type="password" value={pin}
                onChange={(e) => { setPin(e.target.value); setError(''); }}
                onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }}
                onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.14)'; }}
                autoComplete="current-password" disabled={busy}
              />
              {error && <p style={errorStyle} role="alert">{error}</p>}
              <button type="submit" style={btnStyle(!email.trim() || !pin || busy)} disabled={busy || !email.trim() || !pin}>
                {busy ? 'Signing in…' : 'Sign in'}
              </button>
            </form>
            {signupOpen && (
              <>
                <div style={dividerStyle}>
                  <span style={dividerLineStyle} />
                  <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.3)', padding: '0 12px' }}>or</span>
                  <span style={dividerLineStyle} />
                </div>
                <p style={{ textAlign: 'center', fontSize: 14, color: 'rgba(255,255,255,0.4)', margin: 0 }}>
                  Need an account?{' '}
                  <button type="button" onClick={switchToSignUp} style={textLinkStyle}>Sign up</button>
                </p>
              </>
            )}
          </>
        )}

        {/* SIGN UP */}
        {flow === 'signup' && (
          <>
            {signupStep === 'email' && (
              <form onSubmit={handleRegEmailNext}>
                <label style={labelStyle}>Email address</label>
                <input
                  ref={regEmailRef} style={inputStyle} type="email" value={email}
                  onChange={(e) => { setEmail(e.target.value); setError(''); }}
                  onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.14)'; }}
                  autoComplete="email" autoCapitalize="off"
                />
                {error && <p style={errorStyle}>{error}</p>}
                <button type="submit" style={btnStyle(!email.trim())} disabled={!email.trim()}>
                  Continue
                </button>
              </form>
            )}
            {signupStep === 'pin' && (
              <form onSubmit={handleRegPinNext}>
                <p style={{ margin: '0 0 20px', fontSize: 14, color: 'rgba(255,255,255,0.5)', textAlign: 'center' }}>
                  Creating account for <strong style={{ color: 'rgba(255,255,255,0.9)' }}>{email}</strong>
                </p>
                <label style={labelStyle}>Choose a password</label>
                <input
                  ref={regPinRef} style={inputStyle} type="password" value={pin}
                  onChange={(e) => { setPin(e.target.value); setError(''); }}
                  onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.14)'; }}
                  autoComplete="new-password"                />
                {error && <p style={errorStyle}>{error}</p>}
                <button type="submit" style={btnStyle(pin.length < 4)} disabled={pin.length < 4}>
                  Continue
                </button>
              </form>
            )}
            {signupStep === 'confirm' && (
              <form onSubmit={handleRegisterSubmit}>
                <label style={labelStyle}>Confirm password</label>
                <input
                  ref={confirmPinRef} style={inputStyle} type="password" value={confirmPin}
                  onChange={(e) => { setConfirmPin(e.target.value); setError(''); }}
                  onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.14)'; }}
                  autoComplete="new-password" disabled={busy}
                />
                {error && <p style={errorStyle}>{error}</p>}
                <button type="submit" style={btnStyle(confirmPin.length < 4 || busy)} disabled={busy || confirmPin.length < 4}>
                  {busy ? 'Creating…' : 'Create account'}
                </button>
              </form>
            )}
            {signupStep === 'pending' && (
              <div style={{ textAlign: 'center', padding: '12px 0 8px' }}>
                <div style={{ fontSize: 40, marginBottom: 12, color: '#22c55e' }}>&#10003;</div>
                <p style={{ fontSize: 15, color: 'rgba(255,255,255,0.5)', lineHeight: 1.6, margin: 0 }}>
                  Your request has been sent to the admin for approval. Once approved, come back and sign in.
                </p>
              </div>
            )}
            {signupStep !== 'pending' && (
              <>
                <div style={dividerStyle}>
                  <span style={dividerLineStyle} />
                  <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.3)', padding: '0 12px' }}>or</span>
                  <span style={dividerLineStyle} />
                </div>
                <p style={{ textAlign: 'center', fontSize: 14, color: 'rgba(255,255,255,0.4)', margin: 0 }}>
                  Already have an account?{' '}
                  <button type="button" onClick={switchToSignIn} style={textLinkStyle}>Sign in</button>
                </p>
              </>
            )}
          </>
        )}
        </div>
      </div>
    </StartShell>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const columnStyle: React.CSSProperties = {
  minHeight: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
  padding: 'calc(env(safe-area-inset-top) + 32px) 24px calc(env(safe-area-inset-bottom) + 32px)', boxSizing: 'border-box',
  fontFamily: FONT,
};

const formBlockStyle: React.CSSProperties = { width: '100%', maxWidth: 360, marginTop: 28 };

const labelStyle: React.CSSProperties = fieldLabel;

const inputStyle: React.CSSProperties = { ...fieldInput, transition: 'border-color 0.15s' };

const primaryBtnStyle: React.CSSProperties = { ...primaryBtn, display: 'block', marginTop: 24, transition: 'background 0.15s, color 0.15s' };
// Disabled stays readable (about 7:1) instead of fading the blue button.
const DISABLED_BTN = { background: '#2b3658', color: '#c7cedb' } as const;
const btnStyle = (disabled: boolean): React.CSSProperties =>
  disabled ? { ...primaryBtnStyle, ...DISABLED_BTN, cursor: 'not-allowed' } : primaryBtnStyle;
const INVALID_LOGIN = 'Invalid email, username, or password';
const PASSWORD_SPACE_ERROR = "Password can't start or end with a space";

const errorStyle: React.CSSProperties = {
  color: '#f87171',
  fontSize: 13,
  margin: '10px 0 0',
};


const textLinkStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'rgba(255,255,255,0.7)',
  fontWeight: 600,
  fontSize: 'inherit',
  cursor: 'pointer',
  padding: 0,
  fontFamily: 'inherit',
  textDecoration: 'underline',
  textDecorationColor: 'rgba(255,255,255,0.25)',
};

const dividerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  margin: '24px 0',
};

const dividerLineStyle: React.CSSProperties = {
  flex: 1,
  height: 1,
  background: 'rgba(255,255,255,0.1)',
};

/** ?next= from the server's /login redirect: same-site paths only, URL base stripped. */
export function nextFromQuery(search: string): string | null {
  const next = new URLSearchParams(search).get('next');
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.includes('\\')) return null;
  const base = getUrlBase().replace(/\/+$/, '');
  const path = base && (next === base || next.startsWith(base + '/')) ? next.slice(base.length) || '/' : next;
  return path.startsWith('/login') ? '/' : path;
}
