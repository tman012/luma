/**
 * ConnectServiceHost — bottom sheet shown when a feature needs a service
 * that isn't connected yet. TMDB connects in place (key + Test); the others
 * open Settings → Services.
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { CONNECT_EVENT, integrationsStatus, type ServiceId } from './connectService';
import { apiFetch } from '../../apiClient';

const NAMES: Record<ServiceId, string> = {
  tmdb: 'TMDB', radarr: 'Radarr', sonarr: 'Sonarr', qbittorrent: 'qBittorrent',
};

export function ConnectServiceHost() {
  const { user, authenticated } = useAuth();
  const navigate = useNavigate();
  const [service, setService] = useState<ServiceId | null>(null);
  const [key, setKey] = useState('');
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<'test' | 'save' | null>(null);

  useEffect(() => {
    const onEvent = (e: Event) => {
      const svc = (e as CustomEvent<{ service: ServiceId }>).detail?.service;
      if (!svc) return;
      setKey(''); setResult(null); setBusy(null);
      setService(svc);
    };
    window.addEventListener(CONNECT_EVENT, onEvent);
    return () => window.removeEventListener(CONNECT_EVENT, onEvent);
  }, []);

  if (!service || !authenticated) return null;
  const isAdmin = !!user?.is_admin;
  const name = NAMES[service];
  const close = () => setService(null);

  const tmdb = async (save: boolean) => {
    setBusy(save ? 'save' : 'test');
    setResult(null);
    try {
      const res = await apiFetch('/api/integrations/tmdb', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: key.trim(), save }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) { setResult({ ok: false, text: data.error || 'Key not accepted' }); return; }
      if (!save) { setResult({ ok: true, text: 'Key works' }); return; }
      setResult({ ok: true, text: 'Connected' });
      if (data.restarting) {
        await new Promise(r => setTimeout(r, 1500));
        for (let i = 0; i < 40; i++) {
          try { if ((await apiFetch('/health')).ok) break; } catch { /* restarting */ }
          await new Promise(r => setTimeout(r, 750));
        }
        window.location.reload();
        return;
      }
      await integrationsStatus(true);
      window.dispatchEvent(new Event('luma:service-connected'));
      setTimeout(close, 700);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div
      role="dialog" aria-modal="true" aria-label={`Connect ${name}`}
      onClick={close}
      style={{ position: 'fixed', inset: 0, zIndex: 9000, background: 'rgba(5,4,15,0.55)', backdropFilter: 'blur(6px)', WebkitBackdropFilter: 'blur(6px)', display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}
    >
      <div
        onClick={e => e.stopPropagation()}
        className="luma-connect-sheet"
        style={{ width: '100%', maxWidth: 460, margin: '0 auto', background: '#1d1838', borderRadius: '22px 22px 0 0', padding: '10px 22px calc(26px + env(safe-area-inset-bottom))', boxShadow: '0 -10px 40px rgba(0,0,0,0.45)', color: '#fff', fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif' }}
      >
        <style>{`.luma-connect-sheet{animation:lumaSheet .28s cubic-bezier(.16,1,.3,1) both}@keyframes lumaSheet{from{transform:translateY(100%)}to{transform:none}}`}</style>
        <div style={{ width: 36, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.25)', margin: '0 auto 18px' }} />
        <h2 style={{ margin: 0, fontSize: 21, fontWeight: 600, textAlign: 'center' }}>Connect {name}</h2>

        {service === 'tmdb' && isAdmin ? (
          <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <input
              value={key} onChange={e => { setKey(e.target.value); setResult(null); }}
              placeholder="API key" autoComplete="off" spellCheck={false} aria-label="TMDB API key"
              style={{ height: 46, borderRadius: 12, border: '1px solid rgba(255,255,255,0.14)', background: 'rgba(255,255,255,0.06)', color: '#fff', padding: '0 14px', fontSize: 16, outline: 'none' }}
            />
            <div style={{ minHeight: 18, fontSize: 13, textAlign: 'center', color: result ? (result.ok ? '#86efac' : '#fca5a5') : 'transparent' }}>
              {result?.text || '.'}
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button type="button" onClick={() => tmdb(false)} disabled={!key.trim() || !!busy} style={{ ...btn, background: 'rgba(255,255,255,0.1)', color: '#fff' }}>
                {busy === 'test' ? 'Testing…' : 'Test'}
              </button>
              <button type="button" onClick={() => tmdb(true)} disabled={!key.trim() || !!busy} style={btn}>
                {busy === 'save' ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        ) : (
          <div style={{ marginTop: 22, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {isAdmin ? (
              <button type="button" style={btn} onClick={() => { close(); navigate('/settings?tab=services'); }}>
                Open Settings
              </button>
            ) : (
              <p style={{ margin: 0, textAlign: 'center', fontSize: 15, color: 'rgba(255,255,255,0.6)' }}>Not connected</p>
            )}
          </div>
        )}
        <button type="button" onClick={close} style={{ ...btn, marginTop: 10, background: 'none', color: 'rgba(255,255,255,0.6)', fontWeight: 500 }}>
          Not Now
        </button>
      </div>
    </div>
  );
}

const btn: React.CSSProperties = {
  flex: 1, width: '100%', height: 48, borderRadius: 24, border: 'none', background: '#fff', color: '#15102a',
  fontSize: 16, fontWeight: 600, cursor: 'pointer',
};
