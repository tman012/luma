/**
 * UserSetupPage — First-time setup for new users.
 *
 * Three options:
 *   a) "Use shared server" — request access, admin approves via Telegram
 *   b) "Use my own Plex server" — enter custom URL + token
 *   c) "Use both" — enter own URL + token AND request shared access
 *
 * Also handles the "pending approval" state after requesting shared access.
 */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { apiFetch } from '../apiClient';
import { NAVY_BG } from '@/components/start/StartUI';
import { LUMA_BLUE, primaryButtonColors } from '@/ui/lumaButton';

type PlexMode = 'shared' | 'own' | 'both';

export function UserSetupPage() {
  const { user, saveUserSettings, refreshUser, authenticated, loading, plexAccessStatus, logout } = useAuth();
  const navigate = useNavigate();

  const [mode, setMode] = useState<PlexMode>('shared');
  const [plexUrl, setPlexUrl] = useState('');
  const [plexToken, setPlexToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    if (!loading && !authenticated) navigate('/login', { replace: true });
  }, [loading, authenticated, navigate]);

  // If access was already approved (e.g. user returns to page), go home
  useEffect(() => {
    if (plexAccessStatus === 'approved') navigate('/', { replace: true });
  }, [plexAccessStatus, navigate]);

  async function handleSubmit() {
    setBusy(true);
    setError('');

    // Validate own-server fields if needed
    if ((mode === 'own' || mode === 'both') && (!plexUrl.trim() || !plexToken.trim())) {
      setError('Please enter both your Plex URL and token.');
      setBusy(false);
      return;
    }

    try {
      // Save own server config if applicable
      if (mode === 'own' || mode === 'both') {
        const result = await saveUserSettings({
          plex_url: plexUrl.trim(),
          plex_token: plexToken.trim(),
        });
        if (!result.success) {
          setError(result.error || 'Failed to save settings');
          setBusy(false);
          return;
        }
      }

      // Request shared server access if applicable
      if (mode === 'shared' || mode === 'both') {
        const res = await apiFetch('/api/user/request-shared-plex', { method: 'POST' });
        const data = await res.json();
        if (!res.ok || !data.success) {
          setError(data.error || 'Failed to request access');
          setBusy(false);
          return;
        }
      }

      // If own-only, go straight home
      if (mode === 'own') {
        await refreshUser();
        navigate('/', { replace: true });
      } else {
        // Shared or both — show pending state
        await refreshUser();
        setSubmitted(true);
      }
    } catch {
      setError('Network error');
    }
    setBusy(false);
  }

  if (loading) return null;

  // Pending state — waiting for admin approval
  if (submitted || plexAccessStatus === 'pending') {
    return (
      <div style={pageStyle}>
        <div style={cardStyle}>
          <div style={{ textAlign: 'center' }}>
            <h1 style={{ margin: '0 0 8px', fontSize: 20, fontWeight: 700, color: '#fff' }}>
              Waiting for approval
            </h1>
            <p style={{ margin: '0 0 24px', fontSize: 14, color: 'rgba(255,255,255,0.45)', lineHeight: 1.6 }}>
              Your request to access the shared Plex server has been sent.
              You'll receive an email once it's approved.
            </p>
            {mode === 'both' && (
              <button
                type="button"
                onClick={() => navigate('/', { replace: true })}
                style={primaryBtnStyle}
              >
                Continue with my server
              </button>
            )}
            <button
              type="button"
              onClick={async () => { await logout(); navigate('/login', { replace: true }); }}
              style={{ ...linkBtnStyle, marginTop: 16 }}
            >
              Sign out
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Denied state
  if (plexAccessStatus === 'denied') {
    return (
      <div style={pageStyle}>
        <div style={cardStyle}>
          <div style={{ textAlign: 'center' }}>
            <h1 style={{ margin: '0 0 8px', fontSize: 20, fontWeight: 700, color: '#fff' }}>
              Access denied
            </h1>
            <p style={{ margin: '0 0 24px', fontSize: 14, color: 'rgba(255,255,255,0.45)', lineHeight: 1.6 }}>
              Your request to access the shared Plex server was not approved.
              You can still use your own Plex server.
            </p>
            <button
              type="button"
              onClick={() => {
                setMode('own');
                // Reset denied state by clearing it — they can set up own server
              }}
              style={primaryBtnStyle}
            >
              Set up my own server
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={pageStyle}>
      <div style={cardStyle}>
        {/* Header */}
        <h1 style={{ margin: '0 0 6px', fontSize: 22, fontWeight: 700, color: '#fff', textAlign: 'center' }}>
          Welcome, {user?.name || 'there'}
        </h1>
        <p style={{ margin: '0 0 28px', fontSize: 14, color: 'rgba(255,255,255,0.5)', textAlign: 'center' }}>
          Choose how you'd like to connect
        </p>

        {/* Plex server choice */}
        <div style={{ marginBottom: 24 }}>
          <p style={sectionLabelStyle}>Plex server</p>

          <OptionButton
            selected={mode === 'shared'}
            onClick={() => setMode('shared')}
            label="Use shared server"
            desc="Request access to the family Plex library"
          />

          <OptionButton
            selected={mode === 'own'}
            onClick={() => setMode('own')}
            label="Use my own Plex server"
            desc="Connect with your own Plex URL and token"
          />

          <OptionButton
            selected={mode === 'both'}
            onClick={() => setMode('both')}
            label="Use both"
            desc="Connect your server and request shared library access"
          />
        </div>

        {/* Custom Plex fields */}
        {(mode === 'own' || mode === 'both') && (
          <div style={{ marginBottom: 24 }}>
            <label style={fieldLabelStyle}>Plex server URL</label>
            <input
              type="url"
              placeholder="http://localhost:32400"
              value={plexUrl}
              onChange={e => setPlexUrl(e.target.value)}
              style={inputStyle}
            />

            <label style={fieldLabelStyle}>Plex token</label>
            <input
              type="password"
              placeholder="Your Plex authentication token"
              value={plexToken}
              onChange={e => setPlexToken(e.target.value)}
              style={{ ...inputStyle, marginBottom: 8 }}
            />
            <a
              href="https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/"
              target="_blank"
              rel="noreferrer"
              style={{ display: 'inline-block', fontSize: 12, color: LUMA_BLUE, textDecoration: 'none' }}
            >
              How to find your Plex token
            </a>
          </div>
        )}

        {/* Error */}
        {error && (
          <p style={{ margin: '0 0 16px', fontSize: 13, color: '#ef4444', textAlign: 'center' }}>
            {error}
          </p>
        )}

        {/* Submit */}
        <button
          type="button"
          onClick={handleSubmit}
          disabled={busy}
          style={{
            ...primaryBtnStyle,
            opacity: busy ? 0.6 : 1,
            cursor: busy ? 'default' : 'pointer',
          }}
        >
          {busy
            ? 'Submitting...'
            : mode === 'shared'
              ? 'Request access'
              : mode === 'both'
                ? 'Save & request access'
                : 'Save & continue'}
        </button>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function OptionButton({ selected, onClick, label, desc }: {
  selected: boolean;
  onClick: () => void;
  label: string;
  desc: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        width: '100%',
        display: 'flex',
        alignItems: 'flex-start',
        gap: 12,
        padding: '14px 16px',
        background: selected ? 'rgba(255,255,255,0.1)' : 'rgba(255,255,255,0.03)',
        border: selected
          ? '1.5px solid rgba(59,130,246,0.6)'
          : '1px solid rgba(255,255,255,0.08)',
        borderRadius: 12,
        cursor: 'pointer',
        textAlign: 'left',
        marginBottom: 8,
        transition: 'all 0.15s ease',
      }}
    >
      <div
        style={{
          width: 18,
          height: 18,
          borderRadius: '50%',
          border: selected
            ? `5px solid ${LUMA_BLUE}`
            : '2px solid rgba(255,255,255,0.25)',
          flexShrink: 0,
          marginTop: 2,
          transition: 'all 0.15s ease',
        }}
      />
      <div>
        <p style={{ margin: 0, fontSize: 15, fontWeight: 600, color: '#fff' }}>{label}</p>
        <p style={{ margin: '3px 0 0', fontSize: 12, color: 'rgba(255,255,255,0.4)' }}>{desc}</p>
      </div>
    </button>
  );
}


// ---------------------------------------------------------------------------
// Shared styles
// ---------------------------------------------------------------------------

const pageStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: NAVY_BG,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 24,
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
};

const cardStyle: React.CSSProperties = {
  width: '100%',
  maxWidth: 440,
  background: 'rgba(255,255,255,0.06)',
  backdropFilter: 'blur(24px)',
  WebkitBackdropFilter: 'blur(24px)',
  border: '1px solid rgba(255,255,255,0.1)',
  borderRadius: 20,
  padding: '36px 32px',
};

const sectionLabelStyle: React.CSSProperties = {
  margin: '0 0 12px',
  fontSize: 13,
  fontWeight: 600,
  color: 'rgba(255,255,255,0.6)',
};

const fieldLabelStyle: React.CSSProperties = {
  display: 'block',
  marginBottom: 6,
  fontSize: 13,
  fontWeight: 500,
  color: 'rgba(255,255,255,0.6)',
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '10px 14px',
  fontSize: 14,
  color: '#fff',
  background: 'rgba(255,255,255,0.06)',
  border: '1px solid rgba(255,255,255,0.12)',
  borderRadius: 10,
  outline: 'none',
  marginBottom: 14,
  boxSizing: 'border-box',
};

const primaryBtnStyle: React.CSSProperties = {
  width: '100%',
  padding: '11px 0',
  fontSize: 15,
  fontWeight: 600,
  ...primaryButtonColors,
  border: 'none',
  borderRadius: 50,
  cursor: 'pointer',
  transition: 'opacity 0.15s ease',
};

const linkBtnStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  background: 'none',
  border: 'none',
  color: 'rgba(255,255,255,0.4)',
  fontSize: 13,
  cursor: 'pointer',
  padding: 0,
  fontFamily: 'inherit',
  textDecoration: 'underline',
  textDecorationColor: 'rgba(255,255,255,0.15)',
  textAlign: 'center',
};
