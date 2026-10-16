/**
 * LoginPage — Dark themed login matching the landing page aesthetic.
 * Password fields use type="password" for proper keyboard on mobile.
 */

import { useState, useRef, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';

type Flow = 'signin' | 'signup';
type SignInStep = 'email' | 'pin';
type SignUpStep = 'email' | 'pin' | 'confirm' | 'pending';

export function LoginPage() {
  const { login, authenticated, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [flow, setFlow] = useState<Flow>(
    (location.state as any)?.flow === 'signup' ? 'signup' : 'signin',
  );
  const [signinStep, setSigninStep] = useState<SignInStep>('email');
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
    if (!loading && authenticated) {
      const from = (location.state as any)?.from || '/';
      navigate(from, { replace: true });
    }
  }, [authenticated, loading, navigate, location]);

  useEffect(() => {
    if (flow === 'signin') {
      if (signinStep === 'email') setTimeout(() => emailRef.current?.focus(), 100);
      if (signinStep === 'pin') setTimeout(() => pinRef.current?.focus(), 100);
    }
    if (flow === 'signup') {
      if (signupStep === 'email') setTimeout(() => regEmailRef.current?.focus(), 100);
      if (signupStep === 'pin') setTimeout(() => regPinRef.current?.focus(), 100);
      if (signupStep === 'confirm') setTimeout(() => confirmPinRef.current?.focus(), 100);
    }
  }, [flow, signinStep, signupStep]);

  function switchToSignUp() {
    setFlow('signup'); setSignupStep('email'); setEmail(''); setPin(''); setConfirmPin(''); setError(''); setBusy(false);
  }
  function switchToSignIn() {
    setFlow('signin'); setSigninStep('email'); setName(''); setEmail(''); setPin(''); setError(''); setBusy(false);
  }

  async function handleSignInEmail(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = email.trim();
    if (!trimmed) return;
    setBusy(true); setError('');
    try {
      const res = await fetch('/api/auth/check-user', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: trimmed }),
      });
      const data = await res.json();
      if (!data.exists) { setError('No account found with that email.'); setBusy(false); return; }
      setName(data.name || trimmed);
      setPin(''); setSigninStep('pin');
    } catch { setError('Network error. Please try again.'); }
    finally { setBusy(false); }
  }

  async function handlePinSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    if (pin.length < 4) { setError('Password must be at least 4 characters'); return; }
    setBusy(true); setError('');
    const result = await login(email.trim(), pin);
    if (!result.success) { setError(result.error || 'Incorrect password'); setPin(''); setBusy(false); }
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
    if (pin.length < 4) { setError('Password must be at least 4 characters'); return; }
    setConfirmPin(''); setError(''); setSignupStep('confirm');
  }
  async function handleRegisterSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    if (confirmPin.length < 4) { setError('Please re-enter your password'); return; }
    if (confirmPin !== pin) { setError("Passwords don't match. Try again."); setConfirmPin(''); return; }
    setBusy(true); setError('');
    try {
      const res = await fetch('/api/auth/register', {
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
    return <div style={pageStyle}><div style={cardStyle}><p style={{ color: 'rgba(255,255,255,0.4)', fontSize: 14, textAlign: 'center', margin: 0 }}>Loading…</p></div></div>;
  }

  return (
    <div style={pageStyle}>
      <div style={cardStyle}>
        {/* SIGN IN */}
        {flow === 'signin' && (
          <>
            {signinStep === 'email' && (
              <form onSubmit={handleSignInEmail}>
                <label style={labelStyle}>Email address</label>
                <input
                  ref={emailRef} style={inputStyle} type="email" value={email}
                  onChange={(e) => { setEmail(e.target.value); setError(''); }}
                  onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.12)'; }}
                  autoComplete="email" autoCapitalize="off" disabled={busy}
                />
                {error && <p style={errorStyle}>{error}</p>}
                <button type="submit" style={{ ...primaryBtnStyle, opacity: (!email.trim() || busy) ? 0.5 : 1, cursor: (!email.trim() || busy) ? 'not-allowed' : 'pointer' }} disabled={busy || !email.trim()}>
                  {busy ? 'Checking…' : 'Continue'}
                </button>
              </form>
            )}
            {signinStep === 'pin' && (
              <form onSubmit={handlePinSubmit}>
                <p style={{ margin: '0 0 20px', fontSize: 14, color: 'rgba(255,255,255,0.5)', textAlign: 'center' }}>
                  Welcome back, <strong style={{ color: 'rgba(255,255,255,0.9)' }}>{name}</strong>
                </p>
                <label style={labelStyle}>Password</label>
                <input
                  ref={pinRef}
                  style={{ ...inputStyle, WebkitTextSecurity: 'disc' } as any}
                  type="text"
                  value={pin}
                  onChange={(e) => { setPin(e.target.value); setError(''); }}
                  onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.12)'; }}
                  autoComplete="off"
                  name="pin-entry"
                  disabled={busy}
                />
                {error && <p style={errorStyle}>{error}</p>}
                <button type="submit" style={{ ...primaryBtnStyle, opacity: (pin.length < 4 || busy) ? 0.5 : 1, cursor: (pin.length < 4 || busy) ? 'not-allowed' : 'pointer' }} disabled={busy || pin.length < 4}>
                  {busy ? 'Signing In…' : 'Sign In'}
                </button>
                <button type="button" onClick={() => { setSigninStep('email'); setEmail(''); setPin(''); setError(''); }} style={linkBtnStyle}>
                  Use a different account
                </button>
              </form>
            )}
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

        {/* SIGN UP */}
        {flow === 'signup' && (
          <>
            <h2 style={{ margin: '0 0 20px', fontSize: 18, fontWeight: 600, color: 'rgba(255,255,255,0.9)', textAlign: 'center' }}>
              Create Account
            </h2>
            {signupStep === 'email' && (
              <form onSubmit={handleRegEmailNext}>
                <label style={labelStyle}>Email address</label>
                <input
                  ref={regEmailRef} style={inputStyle} type="email" value={email}
                  onChange={(e) => { setEmail(e.target.value); setError(''); }}
                  onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.12)'; }}
                  autoComplete="email" autoCapitalize="off"
                />
                {error && <p style={errorStyle}>{error}</p>}
                <button type="submit" style={{ ...primaryBtnStyle, opacity: !email.trim() ? 0.5 : 1, cursor: !email.trim() ? 'not-allowed' : 'pointer' }} disabled={!email.trim()}>
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
                  onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.12)'; }}
                  autoComplete="new-password"                />
                {error && <p style={errorStyle}>{error}</p>}
                <button type="submit" style={{ ...primaryBtnStyle, opacity: pin.length < 4 ? 0.5 : 1, cursor: pin.length < 4 ? 'not-allowed' : 'pointer' }} disabled={pin.length < 4}>
                  Continue
                </button>
              </form>
            )}
            {signupStep === 'confirm' && (
              <form onSubmit={handleRegisterSubmit}>
                <label style={labelStyle}>Confirm Password</label>
                <input
                  ref={confirmPinRef} style={inputStyle} type="password" value={confirmPin}
                  onChange={(e) => { setConfirmPin(e.target.value); setError(''); }}
                  onFocus={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }}
                  onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.12)'; }}
                  autoComplete="new-password" disabled={busy}
                />
                {error && <p style={errorStyle}>{error}</p>}
                <button type="submit" style={{ ...primaryBtnStyle, opacity: (confirmPin.length < 4 || busy) ? 0.5 : 1, cursor: (confirmPin.length < 4 || busy) ? 'not-allowed' : 'pointer' }} disabled={busy || confirmPin.length < 4}>
                  {busy ? 'Creating…' : 'Create Account'}
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
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const pageStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'radial-gradient(ellipse at 50% 40%, #3a1a3a 0%, #1a0a1e 40%, #0a0410 100%)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: '24px 16px',
  boxSizing: 'border-box',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
};

const cardStyle: React.CSSProperties = {
  width: '100%',
  maxWidth: 400,
  background: 'rgba(255,255,255,0.06)',
  border: '1px solid rgba(255,255,255,0.1)',
  borderRadius: 16,
  padding: '40px 36px 36px',
  boxSizing: 'border-box',
  backdropFilter: 'blur(24px)',
  WebkitBackdropFilter: 'blur(24px)',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 13,
  fontWeight: 500,
  color: 'rgba(255,255,255,0.5)',
  marginBottom: 8,
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '12px 14px',
  fontSize: 16,
  color: 'rgba(255,255,255,0.95)',
  background: 'rgba(255,255,255,0.06)',
  border: '1px solid rgba(255,255,255,0.12)',
  borderRadius: 8,
  outline: 'none',
  boxSizing: 'border-box',
  transition: 'border-color 0.15s',
  fontFamily: 'inherit',
};

const primaryBtnStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  padding: '13px 0',
  marginTop: 20,
  fontSize: 16,
  fontWeight: 700,
  color: '#ffffff',
  background: 'rgba(255,255,255,0.12)',
  border: 'none',
  borderRadius: 50,
  cursor: 'pointer',
  fontFamily: 'inherit',
  transition: 'opacity 0.15s',
};

const errorStyle: React.CSSProperties = {
  color: '#f87171',
  fontSize: 13,
  margin: '10px 0 0',
};

const linkBtnStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  background: 'none',
  border: 'none',
  color: 'rgba(255,255,255,0.4)',
  fontSize: 13,
  cursor: 'pointer',
  padding: '12px 0 0',
  fontFamily: 'inherit',
  textDecoration: 'underline',
  textDecorationColor: 'rgba(255,255,255,0.15)',
  textAlign: 'center',
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
