import { apiFetch } from '../apiClient';
import { primaryButtonColors, LUMA_BLUE } from '@/ui/lumaButton';
/**
 * MySettingsPage — Personal settings for non-admin users.
 *
 * Stacked cards for Plex, Radarr, and Sonarr connections.
 * Each card has its own Save + Test Connection buttons.
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { NAVY_BG } from '@/components/start/StartUI';

type TestStatus = 'idle' | 'testing' | 'success' | 'error';

interface TestResult {
  status: TestStatus;
  message: string;
}

export function MySettingsPage() {
  useBodyScroll();
  const navigate = useNavigate();
  const { userSettings, saveUserSettings } = useAuth();

  // -- Plex state --
  const [useShared, setUseShared] = useState(false);
  const [plexUrl, setPlexUrl] = useState('');
  const [plexToken, setPlexToken] = useState('');
  const [plexTest, setPlexTest] = useState<TestResult>({ status: 'idle', message: '' });
  const [plexSaving, setPlexSaving] = useState(false);
  const [plexSaved, setPlexSaved] = useState(false);

  // -- Radarr state --
  const [radarrUrl, setRadarrUrl] = useState('');
  const [radarrKey, setRadarrKey] = useState('');
  const [radarrTest, setRadarrTest] = useState<TestResult>({ status: 'idle', message: '' });
  const [radarrSaving, setRadarrSaving] = useState(false);
  const [radarrSaved, setRadarrSaved] = useState(false);

  // -- Sonarr state --
  const [sonarrUrl, setSonarrUrl] = useState('');
  const [sonarrKey, setSonarrKey] = useState('');
  const [sonarrTest, setSonarrTest] = useState<TestResult>({ status: 'idle', message: '' });
  const [sonarrSaving, setSonarrSaving] = useState(false);
  const [sonarrSaved, setSonarrSaved] = useState(false);

  // Load initial values from auth context
  useEffect(() => {
    if (userSettings) {
      setUseShared(userSettings.use_shared_server ?? false);
      setPlexUrl(userSettings.plex_url ?? '');
      setPlexToken(userSettings.plex_token ?? '');
      setRadarrUrl(userSettings.radarr_url ?? '');
      setRadarrKey(userSettings.radarr_api_key ?? '');
      setSonarrUrl(userSettings.sonarr_url ?? '');
      setSonarrKey(userSettings.sonarr_api_key ?? '');
    }
  }, [userSettings]);

  // -- Test helpers --
  const testConnection = useCallback(async (
    endpoint: string,
    body: Record<string, string>,
    setResult: (r: TestResult) => void,
  ) => {
    setResult({ status: 'testing', message: '' });
    try {
      const res = await apiFetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setResult({ status: 'success', message: data.message || 'Connection successful' });
      } else {
        setResult({ status: 'error', message: data.error || 'Connection failed' });
      }
    } catch {
      setResult({ status: 'error', message: 'Network error' });
    }
  }, []);

  // -- Save helpers --
  const saveSection = useCallback(async (
    updates: Record<string, unknown>,
    setSaving: (b: boolean) => void,
    setSaved: (b: boolean) => void,
  ) => {
    setSaving(true);
    const result = await saveUserSettings(updates);
    setSaving(false);
    if (result.success) {
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    }
    return result;
  }, [saveUserSettings]);

  return (
    <div style={pageStyle}>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      {/* Fixed header */}
      <div style={headerStyle}>
        <button
          type="button"
          onClick={() => navigate(-1)}
          aria-label="Go back"
          style={backBtnStyle}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path d="M12.5 15L7.5 10L12.5 5" stroke="white" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <h1 style={headerTitleStyle}>My Settings</h1>
        <div style={{ width: 44 }} /> {/* Spacer for centering */}
      </div>

      {/* Content */}
      <div style={contentStyle}>

        {/* Plex Server Card */}
        <div style={cardStyle}>
          <h2 style={cardTitleStyle}>
            <PlexIcon />
            Plex Server
          </h2>

          {/* Use shared toggle */}
          <div style={toggleRowStyle}>
            <label style={toggleLabelStyle} htmlFor="use-shared">
              Use shared server
            </label>
            <button
              id="use-shared"
              type="button"
              role="switch"
              aria-checked={useShared}
              onClick={() => setUseShared(!useShared)}
              style={{
                ...toggleTrackStyle,
                background: useShared ? LUMA_BLUE : 'rgba(255,255,255,0.15)',
              }}
            >
              <div style={{
                ...toggleThumbStyle,
                transform: useShared ? 'translateX(20px)' : 'translateX(0)',
              }} />
            </button>
          </div>

          {!useShared && (
            <>
              <label style={fieldLabelStyle}>Server URL</label>
              <input
                type="url"
                placeholder="http://localhost:32400"
                value={plexUrl}
                onChange={e => setPlexUrl(e.target.value)}
                style={inputStyle}
              />

              <label style={fieldLabelStyle}>Access Token</label>
              <input
                type="password"
                placeholder="Your Plex authentication token"
                value={plexToken}
                onChange={e => setPlexToken(e.target.value)}
                style={inputStyle}
              />
            </>
          )}

          <TestStatusDisplay result={plexTest} />

          <div style={btnRowStyle}>
            {!useShared && (
              <button
                type="button"
                onClick={() => testConnection(
                  '/api/user/test/plex',
                  { url: plexUrl, token: plexToken },
                  setPlexTest,
                )}
                disabled={plexTest.status === 'testing'}
                style={testBtnStyle}
              >
                {plexTest.status === 'testing' ? 'Testing...' : 'Test Connection'}
              </button>
            )}
            <button
              type="button"
              onClick={() => saveSection(
                { use_shared_server: useShared, plex_url: plexUrl, plex_token: plexToken },
                setPlexSaving,
                setPlexSaved,
              )}
              disabled={plexSaving}
              style={saveBtnStyle}
            >
              {plexSaving ? 'Saving...' : plexSaved ? 'Saved' : 'Save'}
            </button>
          </div>
        </div>

        {/* Radarr Card */}
        <div style={cardStyle}>
          <h2 style={cardTitleStyle}>
            <ArrIcon color="#ffc230" />
            Radarr
            <span style={cardSubtitleStyle}>Movies</span>
          </h2>

          <label style={fieldLabelStyle}>Server URL</label>
          <input
            type="url"
            placeholder="http://localhost:7878"
            value={radarrUrl}
            onChange={e => setRadarrUrl(e.target.value)}
            style={inputStyle}
          />

          <label style={fieldLabelStyle}>API Key</label>
          <input
            type="password"
            placeholder="Radarr API key"
            value={radarrKey}
            onChange={e => setRadarrKey(e.target.value)}
            style={inputStyle}
          />

          <TestStatusDisplay result={radarrTest} />

          <div style={btnRowStyle}>
            <button
              type="button"
              onClick={() => testConnection(
                '/api/user/test/radarr',
                { url: radarrUrl, api_key: radarrKey },
                setRadarrTest,
              )}
              disabled={radarrTest.status === 'testing'}
              style={testBtnStyle}
            >
              {radarrTest.status === 'testing' ? 'Testing...' : 'Test Connection'}
            </button>
            <button
              type="button"
              onClick={() => saveSection(
                { radarr_url: radarrUrl, radarr_api_key: radarrKey },
                setRadarrSaving,
                setRadarrSaved,
              )}
              disabled={radarrSaving}
              style={saveBtnStyle}
            >
              {radarrSaving ? 'Saving...' : radarrSaved ? 'Saved' : 'Save'}
            </button>
          </div>
        </div>

        {/* Sonarr Card */}
        <div style={cardStyle}>
          <h2 style={cardTitleStyle}>
            <ArrIcon color="#35c5f4" />
            Sonarr
            <span style={cardSubtitleStyle}>TV Shows</span>
          </h2>

          <label style={fieldLabelStyle}>Server URL</label>
          <input
            type="url"
            placeholder="http://localhost:8989"
            value={sonarrUrl}
            onChange={e => setSonarrUrl(e.target.value)}
            style={inputStyle}
          />

          <label style={fieldLabelStyle}>API Key</label>
          <input
            type="password"
            placeholder="Sonarr API key"
            value={sonarrKey}
            onChange={e => setSonarrKey(e.target.value)}
            style={inputStyle}
          />

          <TestStatusDisplay result={sonarrTest} />

          <div style={btnRowStyle}>
            <button
              type="button"
              onClick={() => testConnection(
                '/api/user/test/sonarr',
                { url: sonarrUrl, api_key: sonarrKey },
                setSonarrTest,
              )}
              disabled={sonarrTest.status === 'testing'}
              style={testBtnStyle}
            >
              {sonarrTest.status === 'testing' ? 'Testing...' : 'Test Connection'}
            </button>
            <button
              type="button"
              onClick={() => saveSection(
                { sonarr_url: sonarrUrl, sonarr_api_key: sonarrKey },
                setSonarrSaving,
                setSonarrSaved,
              )}
              disabled={sonarrSaving}
              style={saveBtnStyle}
            >
              {sonarrSaving ? 'Saving...' : sonarrSaved ? 'Saved' : 'Save'}
            </button>
          </div>
        </div>

        {/* Bottom spacer for safe area */}
        <div style={{ height: 40 }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function TestStatusDisplay({ result }: { result: TestResult }) {
  if (result.status === 'idle') return null;

  const color = result.status === 'success'
    ? '#22c55e'
    : result.status === 'error'
      ? '#ef4444'
      : 'rgba(255,255,255,0.5)';

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      padding: '8px 12px',
      marginBottom: 14,
      borderRadius: 8,
      background: result.status === 'success'
        ? 'rgba(34,197,94,0.1)'
        : result.status === 'error'
          ? 'rgba(239,68,68,0.1)'
          : 'rgba(255,255,255,0.05)',
    }}>
      {result.status === 'testing' && (
        <div style={spinnerStyle} />
      )}
      <span style={{ fontSize: 13, color, lineHeight: 1.4 }}>
        {result.status === 'testing' ? 'Testing connection...' : result.message}
      </span>
    </div>
  );
}

function PlexIcon() {
  return (
    <div style={{
      width: 24,
      height: 24,
      borderRadius: 6,
      background: '#e5a00d',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      flexShrink: 0,
    }}>
      <span style={{ fontSize: 13, fontWeight: 800, color: '#000', lineHeight: 1 }}>P</span>
    </div>
  );
}

function ArrIcon({ color }: { color: string }) {
  return (
    <div style={{
      width: 24,
      height: 24,
      borderRadius: 6,
      background: color,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      flexShrink: 0,
    }}>
      <span style={{ fontSize: 11, fontWeight: 800, color: '#000', lineHeight: 1 }}>A</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const pageStyle: React.CSSProperties = {
  minHeight: '100dvh',
  background: NAVY_BG,
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
};

const headerStyle: React.CSSProperties = {
  position: 'sticky',
  top: 0,
  zIndex: 50,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: '12px 16px',
  paddingTop: 'max(12px, env(safe-area-inset-top))',
  background: 'rgba(21,16,42,0.85)',
  backdropFilter: 'blur(20px)',
  WebkitBackdropFilter: 'blur(20px)',
  borderBottom: '1px solid rgba(255,255,255,0.06)',
};

const backBtnStyle: React.CSSProperties = {
  width: 44,
  height: 44,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  borderRadius: 12,
  padding: 0,
};

const headerTitleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 17,
  fontWeight: 700,
  color: '#fff',
  letterSpacing: '-0.01em',
};

const contentStyle: React.CSSProperties = {
  maxWidth: 520,
  margin: '0 auto',
  padding: '20px 16px',
  display: 'flex',
  flexDirection: 'column',
  gap: 16,
};

const cardStyle: React.CSSProperties = {
  background: 'rgba(255,255,255,0.05)',
  border: '1px solid rgba(255,255,255,0.08)',
  borderRadius: 16,
  padding: '20px 20px 16px',
};

const cardTitleStyle: React.CSSProperties = {
  margin: '0 0 18px',
  fontSize: 16,
  fontWeight: 600,
  color: '#fff',
  display: 'flex',
  alignItems: 'center',
  gap: 10,
};

const cardSubtitleStyle: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 500,
  color: 'rgba(255,255,255,0.35)',
  marginLeft: 4,
};

const fieldLabelStyle: React.CSSProperties = {
  display: 'block',
  marginBottom: 6,
  fontSize: 13,
  fontWeight: 500,
  color: 'rgba(255,255,255,0.55)',
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '11px 14px',
  fontSize: 14,
  color: '#fff',
  background: 'rgba(255,255,255,0.1)',
  border: '1px solid rgba(255,255,255,0.12)',
  borderRadius: 12,
  outline: 'none',
  marginBottom: 14,
  boxSizing: 'border-box',
};

const toggleRowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  marginBottom: 16,
  padding: '4px 0',
};

const toggleLabelStyle: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 500,
  color: 'rgba(255,255,255,0.75)',
  cursor: 'pointer',
};

const toggleTrackStyle: React.CSSProperties = {
  position: 'relative',
  width: 46,
  height: 26,
  borderRadius: 13,
  border: 'none',
  cursor: 'pointer',
  padding: 3,
  transition: 'background 0.2s',
  flexShrink: 0,
};

const toggleThumbStyle: React.CSSProperties = {
  width: 20,
  height: 20,
  borderRadius: '50%',
  background: '#fff',
  transition: 'transform 0.2s',
};

const btnRowStyle: React.CSSProperties = {
  display: 'flex',
  gap: 10,
  alignItems: 'center',
};

const testBtnStyle: React.CSSProperties = {
  flex: 1,
  padding: '10px 0',
  fontSize: 13,
  fontWeight: 600,
  color: 'rgba(255,255,255,0.8)',
  background: 'rgba(255,255,255,0.08)',
  border: '1px solid rgba(255,255,255,0.12)',
  borderRadius: 10,
  cursor: 'pointer',
  transition: 'background 0.15s',
};

const saveBtnStyle: React.CSSProperties = {
  flex: 1,
  padding: '10px 0',
  fontSize: 13,
  fontWeight: 600,
  ...primaryButtonColors,
  border: 'none',
  borderRadius: 10,
  cursor: 'pointer',
  transition: 'background 0.15s',
};

const spinnerStyle: React.CSSProperties = {
  width: 14,
  height: 14,
  border: '2px solid rgba(255,255,255,0.15)',
  borderTopColor: 'rgba(255,255,255,0.6)',
  borderRadius: '50%',
  animation: 'spin 0.7s linear infinite',
  flexShrink: 0,
};
