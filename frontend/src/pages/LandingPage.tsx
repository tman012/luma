/**
 * LandingPage — Plex-style splash screen with Luma branding.
 */

import { useNavigate } from 'react-router';
import { assetUrl } from '@/urlHelpers';
import { useAuth } from '@/contexts/AuthContext';
import { NAVY_BG } from '@/components/start/StartUI';

export function LandingPage() {
  const navigate = useNavigate();
  const { signupOpen } = useAuth();

  return (
    <div className="luma-bleed"
      style={{
        position: 'fixed',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: NAVY_BG,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
        overflow: 'hidden',
      }}
    >
      {/* Centered wordmark */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <img
          src={assetUrl('/static/images/luma_text.webp')}
          alt="Luma"
          style={{
            width: 'clamp(380px, 90vw, 780px)',
            height: 'auto',
            objectFit: 'contain',
            userSelect: 'none',
          }}
        />
      </div>

      {/* Bottom buttons */}
      <div
        style={{
          width: '100%',
          maxWidth: 400,
          padding: '0 28px 48px',
          boxSizing: 'border-box',
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        }}
      >
        <button
          onClick={() => navigate('/login', { state: { flow: 'signin' } })}
          style={btnStyle}
        >
          Sign in
        </button>

        {signupOpen && (
          <button
            onClick={() => navigate('/login', { state: { flow: 'signup' } })}
            style={btnStyle}
          >
            Sign up
          </button>
        )}

        <p
          style={{
            marginTop: 14,
            fontSize: 11,
            color: 'rgba(255,255,255,0.35)',
            textAlign: 'center',
            lineHeight: 1.5,
            padding: '0 8px',
          }}
        >
          By creating an account or continuing to use Luma, you acknowledge and agree that you have accepted the Terms of Service and have reviewed the Privacy Policy.
        </p>
      </div>
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  width: '100%',
  padding: '9px 0',
  fontSize: 15,
  fontWeight: 600,
  color: '#ffffff',
  background: 'rgba(255,255,255,0.08)',
  border: 'none',
  borderRadius: 50,
  cursor: 'pointer',
  fontFamily: 'inherit',
  letterSpacing: '0.01em',
};
