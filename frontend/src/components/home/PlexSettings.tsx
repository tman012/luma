/** Settings > Plex (owner only): connect a Plex server after setup (or
 * switch to another one). Same Plex sign-in as Get Started: a pop-up when
 * possible, else this tab, which Plex returns to /settings/plex. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '@/apiClient';
import { openPlexPopup, plexAuthMode } from '@/utils/plexAuth';
import { FONT } from '@/components/start/StartUI';
import { LUMA_BLUE } from '@/ui/lumaButton';

interface Server { machine_id: string; name: string }
interface Status { connected: boolean; server: string }

async function call<T>(url: string, init?: RequestInit): Promise<{ ok: boolean; status: number; data: T }> {
  try {
    const res = await apiFetch(url, init);
    return { ok: res.ok, status: res.status, data: (await res.json().catch(() => ({}))) as T };
  } catch {
    return { ok: false, status: 0, data: { error: "Couldn't reach Luma" } as T };
  }
}
const post = <T,>(url: string, body: unknown) =>
  call<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/** True for the owner (the Plex settings answer only them); null while checking. */
export function usePlexOwner(admin: boolean): boolean | null {
  const [owner, setOwner] = useState<boolean | null>(admin ? null : false);
  useEffect(() => {
    if (!admin) { setOwner(false); return; }
    let alive = true;
    call('/api/settings/plex').then(r => { if (alive) setOwner(r.ok); });
    return () => { alive = false; };
  }, [admin]);
  return owner;
}

type Phase = 'idle' | 'waiting' | 'servers' | 'connecting';

export function PlexSettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [servers, setServers] = useState<Server[]>([]);
  const [error, setError] = useState('');
  const timer = useRef<number | null>(null);
  const popup = useRef<Window | null>(null);

  const stop = () => { if (timer.current !== null) window.clearTimeout(timer.current); timer.current = null; };
  const closePopup = () => { try { popup.current?.close(); } catch { /* gone */ } popup.current = null; };

  const load = useCallback(() => {
    call<Status & { error?: string }>('/api/settings/plex').then(r => {
      if (r.ok) setStatus({ connected: !!r.data.connected, server: r.data.server || '' });
      else setError("Couldn't load Plex");
    });
  }, []);

  const choose = useCallback(async (s: Server) => {
    setError('');
    setPhase('connecting');
    const r = await post<{ error?: string }>('/api/settings/plex/server', { machine_id: s.machine_id });
    if (r.ok) { setPhase('idle'); setServers([]); load(); return; }
    setPhase(servers.length > 1 ? 'servers' : 'idle');
    setError(r.data.error || `Couldn't connect to ${s.name}`);
  }, [load, servers.length]);

  const poll = useCallback((first = false) => {
    stop();
    const tick = async () => {
      const r = await call<{ status?: string; servers?: Server[]; error?: string }>('/api/settings/plex/pin');
      if (!r.ok) {
        if (r.status === 0 || r.status >= 500) { timer.current = window.setTimeout(tick, 3000); return; }
        closePopup(); setPhase('idle'); setError(r.data.error || 'Something went wrong'); return;
      }
      if (r.data.status === 'none') { setPhase('idle'); return; }
      if (r.data.status === 'waiting') { setPhase('waiting'); timer.current = window.setTimeout(tick, 2000); return; }
      if (r.data.status === 'expired') { closePopup(); setPhase('idle'); setError('Sign-in timed out. Try again.'); return; }
      closePopup();
      const found = r.data.servers || [];
      setServers(found);
      if (found.length === 0) { setPhase('idle'); setError('No Plex servers found on this account.'); }
      else if (found.length === 1) choose(found[0]!);
      else setPhase('servers');
    };
    timer.current = window.setTimeout(tick, first ? 0 : 1500);
  }, [choose]);

  // Load status; resume a sign-in this owner started (e.g. Plex sent this tab back).
  useEffect(() => {
    load();
    poll(true);
    return () => { stop(); closePopup(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const connect = async () => {
    setError('');
    stop();
    const win = openPlexPopup();
    const r = await post<{ auth_url?: string; error?: string }>('/api/settings/plex/pin', { mode: plexAuthMode(win) });
    if (!r.ok || !r.data.auth_url) {
      win?.close();
      setError(r.data.error || "Couldn't reach Plex. Try again.");
      return;
    }
    setPhase('waiting');
    if (win) { popup.current = win; win.location.href = r.data.auth_url; poll(); }
    else window.location.href = r.data.auth_url;
  };

  const cancel = () => { stop(); closePopup(); setPhase('idle'); setError(''); };

  return (
    <section data-testid="plex-settings">
      <h2 style={sectionTitle}>Server</h2>
      {error && <p role="alert" style={{ color: '#ff8a8a', fontSize: 14, margin: '0 0 12px' }}>{error}</p>}
      {status === null ? (
        <div style={card}><div className="luma-shimmer" style={{ height: 60 }} /></div>
      ) : phase === 'servers' ? (
        <div style={card} data-testid="plex-servers">
          {servers.map(s => (
            <button key={s.machine_id} type="button" style={pickRow} onClick={() => choose(s)}>
              <span style={name}>{s.name}</span>
              <span aria-hidden="true" style={{ opacity: 0.4, color: '#fff' }}>›</span>
            </button>
          ))}
        </div>
      ) : (
        <div style={card}>
          <div style={rowBox}>
            <span style={name} data-testid="plex-server-name">{status.connected ? (status.server || 'Plex') : 'Not connected'}</span>
          </div>
        </div>
      )}
      <div style={{ display: 'flex', gap: 12, marginTop: 16, alignItems: 'center' }}>
        {phase === 'waiting' ? (
          <>
            <span role="status" style={{ color: 'rgba(255,255,255,0.7)', fontSize: 15, fontFamily: FONT }}>Waiting for Plex…</span>
            <button type="button" style={ghostBtn} onClick={cancel}>Cancel</button>
          </>
        ) : phase === 'connecting' ? (
          <span role="status" style={{ color: 'rgba(255,255,255,0.7)', fontSize: 15, fontFamily: FONT }}>Connecting…</span>
        ) : phase === 'idle' && status !== null ? (
          <button type="button" style={primary} onClick={connect}>
            {status.connected ? 'Change server' : 'Connect Plex'}
          </button>
        ) : null}
      </div>
    </section>
  );
}

const sectionTitle: React.CSSProperties = {
  margin: '24px 4px 8px', fontSize: 13, fontWeight: 600, color: 'rgba(255,255,255,0.5)', fontFamily: FONT,
};
const card: React.CSSProperties = {
  borderRadius: 16, overflow: 'hidden', background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
  backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)', display: 'flex', flexDirection: 'column',
};
const rowBox: React.CSSProperties = { display: 'flex', alignItems: 'center', minHeight: 56, padding: '0 16px' };
const name: React.CSSProperties = { flex: 1, fontSize: 16, fontFamily: FONT, color: '#fff', textAlign: 'left' };
const pickRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 12, width: '100%', minHeight: 56, padding: '0 16px', border: 'none',
  borderBottom: '1px solid rgba(255,255,255,0.06)', background: 'none', cursor: 'pointer',
};
const primary: React.CSSProperties = {
  minHeight: 44, padding: '0 22px', borderRadius: 22, border: 'none', background: LUMA_BLUE, color: '#fff',
  fontSize: 15, fontWeight: 600, fontFamily: FONT, cursor: 'pointer',
};
const ghostBtn: React.CSSProperties = {
  minHeight: 44, padding: '0 18px', borderRadius: 22, border: '1px solid rgba(255,255,255,0.2)', background: 'none',
  color: 'rgba(255,255,255,0.85)', fontSize: 15, fontFamily: FONT, cursor: 'pointer',
};
