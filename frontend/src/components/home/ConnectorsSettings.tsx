/** Settings > Connectors (owner only). Settings premium pass: one grouped list of the real
 * connectors only — Media server (Plex, moved here from its own section), Download Client (qBittorrent),
 * Home (Home Assistant), Metadata (TMDB, OMDb, TheTVDB, Trakt) — each with its vendor logo
 * (static/images/connectors, same origin) and Connected / Not set up (saved config, not a live check). A row opens its form in a modal.
 * Keys stay on the server; the browser only learns whether one is saved and its last 4
 * (never any of the qBittorrent password). Web Push lives under Settings > Notifications. */
import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { apiFetch } from '@/apiClient';
import { FONT } from '@/components/start/StartUI';
import { LUMA_BLUE } from '@/ui/lumaButton';
import { NoticeToast } from '@/components/start/SetupToast';
import { useKioskWidth } from '@/components/home/KioskHome';
import { QbitConnector } from '@/components/home/QbitConnector';
import { HaConnector } from '@/components/home/HaConnector';
import { PlexSettings } from '@/components/home/PlexSettings';
import { assetUrl } from '@/urlHelpers';

type Masked = { set: boolean; last4: string; pin_set?: boolean };
interface View { tmdb: Masked; omdb?: Masked; trakt?: Masked; tvdb?: Masked }
type Id = 'tmdb' | 'omdb' | 'trakt' | 'tvdb';
const MASK = '********';

async function call<T>(url: string, init?: RequestInit): Promise<{ ok: boolean; data: T }> {
  try {
    const res = await apiFetch(url, init);
    return { ok: res.ok, data: (await res.json().catch(() => ({}))) as T };
  } catch {
    return { ok: false, data: { error: "Couldn't reach Luma" } as T };
  }
}
const send = <T,>(url: string, method: string, body: unknown) =>
  call<T>(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

const GROUPS: { id: Id; title: string; field: string; body: string; removeTitle: string; pin?: boolean }[] = [
  { id: 'tmdb', title: 'TMDB', field: 'API key', body: 'api_key', removeTitle: 'Remove TMDB API key?' },
  { id: 'omdb', title: 'OMDb', field: 'API key', body: 'api_key', removeTitle: 'Remove OMDb API key?' },
  { id: 'tvdb', title: 'TheTVDB', field: 'API key', body: 'api_key', removeTitle: 'Remove TheTVDB API key?', pin: true },
  { id: 'trakt', title: 'Trakt', field: 'Client ID', body: 'client_id', removeTitle: 'Remove Trakt client ID?' },
];

export type ConnectorId = 'plex' | 'qbittorrent' | 'homeassistant' | Id;
/** The real connectors, in list order (Designer round 2). Nothing else is shown here. */
export const CONNECTORS: { id: ConnectorId; title: string; group: string; logo?: string; tile: string; pad: number }[] = [
  { id: 'plex', title: 'Plex', group: 'Media server', logo: 'plex.svg', tile: '#1f1f1f', pad: 9 },
  { id: 'qbittorrent', title: 'qBittorrent', group: 'Download Client', logo: 'qbittorrent.svg', tile: '#ffffff', pad: 5 },
  { id: 'homeassistant', title: 'Home Assistant', group: 'Smart Home', logo: 'homeassistant.svg', tile: '#18BCF2', pad: 8 },
  { id: 'tmdb', title: 'TMDB', group: 'Metadata', logo: 'tmdb.svg', tile: '#0d253f', pad: 7 },
  { id: 'omdb', title: 'OMDb', group: 'Metadata', tile: '#f5c518', pad: 0 },
  { id: 'tvdb', title: 'TheTVDB', group: 'Metadata', logo: 'tvdb.svg', tile: '#0b1b24', pad: 5 },
  { id: 'trakt', title: 'Trakt', group: 'Metadata', logo: 'trakt.svg', tile: '#ED1C24', pad: 8 },
];
const CONNECTOR_GROUPS = ['Media server', 'Download Client', 'Smart Home', 'Metadata'];

type Status = { on: boolean; sub: string };
const hostOf = (u: string) => { try { return new URL(u).hostname; } catch { return ''; } };

export function ConnectorLogo({ id, size = 40 }: { id: ConnectorId; size?: number }) {
  const c = CONNECTORS.find(x => x.id === id)!;
  const pad = Math.round(c.pad * size / 40);
  return (
    <span aria-hidden="true" data-testid={`connector-logo-${id}`}
      style={{ width: size, height: size, borderRadius: Math.round(size * 0.26), background: c.tile, padding: pad, boxSizing: 'border-box',
        flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden',
        boxShadow: 'inset 0 0 0 1px rgba(255,255,255,0.08)' }}>
      {c.logo
        ? <img src={assetUrl(`/static/images/connectors/${c.logo}`)} alt="" draggable={false} style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }} />
        : <span style={{ font: `800 ${Math.round(size * 0.275)}px/1 ${FONT}`, color: '#111', letterSpacing: -0.3 }}>OMDb</span>}
    </span>
  );
}

/** initial: open this connector's modal at once (/settings/plex → #plex, Plex sign-in's return path);
 * onInitialClosed: called when that modal closes (drops the #plex hash). */
export function ConnectorsSettings({ initial, onInitialClosed }: { initial?: ConnectorId; onInitialClosed?: () => void } = {}) {
  const [view, setView] = useState<View | null>(null);
  const [plex, setPlex] = useState<Status>({ on: false, sub: '' });
  const [qbit, setQbit] = useState<Status>({ on: false, sub: '' });
  const [ha, setHa] = useState<Status>({ on: false, sub: '' });
  const [open, setOpen] = useState<ConnectorId | null>(initial || null);
  const [toast, setToast] = useState('');
  const wide = useKioskWidth();

  const load = useCallback(async () => {
    const [k, p, q, h] = await Promise.all([
      call<View>('/api/settings/connectors'),
      call<{ connected?: boolean; server?: string }>('/api/settings/plex'),
      call<{ url?: string; password?: { set: boolean } }>('/api/settings/connectors/qbittorrent'),
      call<{ url?: string; access_token?: { set: boolean } }>('/api/settings/connectors/homeassistant'),
    ]);
    if (k.ok) setView(k.data);
    if (p.ok) setPlex({ on: !!p.data.connected, sub: p.data.connected ? (p.data.server || '') : '' });
    if (q.ok) setQbit({ on: !!q.data.url && !!q.data.password?.set, sub: hostOf(q.data.url || '') });
    if (h.ok) setHa({ on: !!h.data.url && !!h.data.access_token?.set, sub: hostOf(h.data.url || '') });
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!view) return null;
  const status = (id: ConnectorId): Status => id === 'plex' ? plex : id === 'qbittorrent' ? qbit : id === 'homeassistant' ? ha
    : { on: !!view[id]?.set, sub: '' };
  const close = () => {
    setOpen(null);
    if (open === initial) onInitialClosed?.();
    load();
  };
  const saved = (text: string) => { setToast(text); load(); };
  const current = open ? CONNECTORS.find(c => c.id === open)! : null;

  return (
    <section data-testid="connectors-section" style={{ padding: wide ? 0 : '0 20px', display: 'flex', flexDirection: 'column', gap: 24 }}>
      {toast && <NoticeToast key={toast} text={toast} onDone={() => setToast('')} />}
      {CONNECTOR_GROUPS.map(g => (
        <div key={g} data-testid="connector-group">
          <h2 style={groupHeader}>{g}</h2>
          <div style={listCard}>
            {CONNECTORS.filter(c => c.group === g).map((c, i) => {
              const st = status(c.id);
              return (
                <button key={c.id} type="button" data-testid={`connector-row-${c.id}`} className="luma-conn-row"
                  onClick={() => setOpen(c.id)} style={{ ...listRow, borderTop: i ? '1px solid rgba(255,255,255,0.07)' : 'none' }}>
                  <ConnectorLogo id={c.id} />
                  <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2, textAlign: 'left' }}>
                    <span style={{ fontSize: 16, fontWeight: 600, color: 'rgba(255,255,255,0.95)' }}>{c.title}</span>
                    {st.sub && <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.5)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{st.sub}</span>}
                  </span>
                  <span data-testid={`connector-status-${c.id}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 14, color: 'rgba(255,255,255,0.7)' }}>
                    <span style={dot(st.on)} aria-hidden="true" />{st.on ? 'Connected' : 'Not set up'}
                  </span>
                  <span aria-hidden="true" style={{ color: 'rgba(255,255,255,0.4)', fontSize: 18, marginLeft: 4 }}>›</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
      <style>{'.luma-conn-row:hover{background:rgba(255,255,255,.04)}.luma-conn-row:focus-visible{outline:2px solid #60a5fa;outline-offset:-2px}'}</style>
      {current && (
        <ConnectorModal id={current.id} title={current.title} on={status(current.id).on} onClose={close}>
          {current.id === 'plex' ? <PlexSettings bare />
            : current.id === 'qbittorrent' ? <QbitConnector bare onSaved={saved} />
            : current.id === 'homeassistant' ? <HaConnector bare onSaved={saved} />
            : (() => {
              const g = GROUPS.find(x => x.id === current.id)!;
              return <ConnectorGroup bare group={g} masked={view[g.id] || { set: false, last4: '' }}
                onSaved={(v, removed) => { setView(v); setToast(removed ? 'Removed' : 'Saved'); }} />;
            })()}
        </ConnectorModal>
      )}
    </section>
  );
}

/** 560px connector modal (Designer round 2): logo + name, ✕; status chip; the form; Done. */
function ConnectorModal({ id, title, on, onClose, children }: { id: ConnectorId; title: string; on: boolean; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    // Esc closes the modal, unless a confirm inside it is open (that confirm handles Esc itself).
    const k = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || document.querySelector('[role="alertdialog"]')) return;
      e.stopPropagation(); onClose();
    };
    document.addEventListener('keydown', k, true);
    return () => document.removeEventListener('keydown', k, true);
  }, [onClose]);
  return createPortal(
    <div className="luma-fullscreen luma-bleed" data-testid="connector-modal-scrim" onPointerDown={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(10,8,24,.6)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, fontFamily: FONT }}>
      <div role="dialog" aria-modal="true" aria-label={title} data-testid={`connector-modal-${id}`}
        style={{ width: 560, maxWidth: '100%', maxHeight: 'calc(100vh - 32px)', overflowY: 'auto', boxSizing: 'border-box', padding: 28,
          borderRadius: 24, background: 'linear-gradient(180deg, rgba(34,28,70,.98), rgba(24,19,50,.98))', border: '1px solid rgba(255,255,255,0.1)',
          boxShadow: '0 40px 100px rgba(0,0,0,.6)', color: 'rgba(255,255,255,0.95)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 20 }}>
          <ConnectorLogo id={id} size={44} />
          <h2 style={{ margin: 0, flex: 1, fontSize: 20, fontWeight: 600 }}>{title}</h2>
          <button type="button" aria-label="Close" data-testid="connector-modal-close" onClick={onClose}
            style={{ width: 36, height: 36, borderRadius: 18, border: 'none', background: 'rgba(255,255,255,0.08)', color: 'rgba(255,255,255,0.85)', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', minHeight: 52, padding: '0 16px', marginBottom: 16, borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)' }}>
          <span style={{ fontSize: 15 }}>Status</span>
          <span data-testid="connector-modal-status" style={{ height: 28, padding: '0 12px', borderRadius: 999, display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13, fontWeight: 600,
            background: on ? 'rgba(34,197,94,0.14)' : 'rgba(255,255,255,0.08)', color: on ? '#86efac' : 'rgba(255,255,255,0.6)' }}>
            <span style={dot(on)} aria-hidden="true" />{on ? 'Connected' : 'Not set up'}
          </span>
        </div>
        {children}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 20 }}>
          <button type="button" data-testid="connector-modal-done" onClick={onClose}
            style={{ ...primary, borderRadius: 999, minWidth: 120 }}>Done</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function ConnectorGroup({ group, masked, onSaved, bare = false }: {
  group: (typeof GROUPS)[number]; masked: Masked; onSaved: (v: View, removed: boolean) => void; bare?: boolean;
}) {
  const { id } = group;
  const [key, setKey] = useState('');
  const [pin, setPin] = useState('');
  const [show, setShow] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  useEffect(() => {
    if (!result) return;
    const t = window.setTimeout(() => setResult(null), 4000);
    return () => window.clearTimeout(t);
  }, [result]);

  const saved = masked.set;
  const trimmed = key.trim();
  const pinTrim = pin.trim();
  const pinBody = group.pin && pinTrim ? { pin: pinTrim } : {};
  const dirty = !!trimmed || (!!group.pin && !!pinTrim && saved);

  const test = async () => {
    setTesting(true);
    const r = await send<{ ok?: boolean; message?: string; error?: string }>(
      `/api/settings/connectors/${id}/test`, 'POST', { ...(trimmed ? { [group.body]: trimmed } : {}), ...pinBody });
    setTesting(false);
    setResult(r.data.ok ? { ok: true, text: 'Connected' } : { ok: false, text: r.data.error || 'Not connected' });
  };

  const save = async (value: string) => {
    setSaving(true);
    if (!value) setResult(null);       // Remove: drop an earlier Connected at once
    const body = value ? { [group.body]: value, ...pinBody } : { [group.body]: '' };
    const r = await send<View & { error?: string }>(`/api/settings/connectors/${id}`, 'PUT', body);
    setSaving(false);
    setConfirmRemove(false);
    if (!r.ok) { setResult({ ok: false, text: r.data.error || "Couldn't save" }); return; }
    setKey('');
    setPin('');
    setShow(false);
    onSaved(r.data, !value);
  };

  return (
    <div data-testid={`connector-${id}`}>
      {!bare && <h2 style={groupHeader}>{group.title}</h2>}
      <div style={card}>
        <label htmlFor={`${id}-key`} style={label}>
          {group.field}
          <span style={dot(saved)} aria-hidden="true" data-testid={`${id}-status`} />
        </label>
        <div style={{ position: 'relative' }}>
          <input
            id={`${id}-key`} data-testid={`${id}-key`} style={input} autoComplete="off" spellCheck={false}
            type={show ? 'text' : 'password'} value={key}
            placeholder={saved ? `••••${masked.last4}` : ''}
            onChange={e => { setKey(e.target.value); setConfirmRemove(false); }}
          />
          <button type="button" style={eye} aria-label={show ? 'Hide' : 'Show'} onClick={() => setShow(s => !s)}>
            {show ? 'Hide' : 'Show'}
          </button>
        </div>
        {group.pin && (
          <>
            <label htmlFor={`${id}-pin`} style={{ ...label, marginTop: 12 }}>Subscriber PIN (optional)</label>
            <input id={`${id}-pin`} data-testid={`${id}-pin`} style={{ ...input, paddingRight: 14 }} autoComplete="off" spellCheck={false}
              type="password" value={pin} placeholder={masked.pin_set ? '••••••••' : ''}
              onChange={e => { setPin(e.target.value); setConfirmRemove(false); }} />
          </>
        )}
        <div style={actions}>
          <button type="button" style={ghostBtn} disabled={testing || (!trimmed && !saved)} onClick={test} data-testid={`${id}-test`}>
            {testing ? 'Testing…' : 'Test'}
          </button>
          <button type="button" style={{ ...primary, opacity: dirty && !saving ? 1 : 0.4 }}
                  disabled={!dirty || saving} onClick={() => save(trimmed || MASK)} data-testid={`${id}-save`}>
            Save
          </button>
          {saved && !dirty && (
            <button type="button" style={dangerBtn} onClick={() => setConfirmRemove(true)} data-testid={`${id}-remove`}>Remove</button>
          )}
          {result && (
            <span role="status" style={{ fontSize: 13, color: result.ok ? '#22c55e' : '#f87171' }} data-testid={`${id}-result`}>
              {result.text}
            </span>
          )}
        </div>
      </div>
      {confirmRemove && saved && !trimmed && (
        <div role="dialog" aria-modal="true" className="luma-bleed" style={dialogBack} onClick={() => setConfirmRemove(false)}>
          <div style={dialog} onClick={e => e.stopPropagation()}>
            <p style={{ margin: '0 0 20px', fontSize: 17, fontWeight: 600 }}>{group.removeTitle}</p>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button type="button" style={ghostBtn} onClick={() => setConfirmRemove(false)}>Cancel</button>
              <button type="button" style={{ ...primary, background: '#ef4444' }} onClick={() => save('')}>Remove</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const groupHeader: React.CSSProperties = {
  margin: '0 4px 8px', fontSize: 13, fontWeight: 600, color: 'rgba(255,255,255,0.5)', fontFamily: FONT,
};
const card: React.CSSProperties = {
  borderRadius: 16, padding: 20, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
  display: 'flex', flexDirection: 'column', gap: 6, fontFamily: FONT,
};
const label: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 500, color: 'rgba(255,255,255,0.7)',
};
const dot = (on: boolean): React.CSSProperties => ({
  width: 8, height: 8, borderRadius: 4, background: on ? '#22c55e' : 'rgba(255,255,255,0.3)',
});
const input: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', height: 44, padding: '0 64px 0 14px', borderRadius: 10,
  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.12)', color: '#fff',
  fontSize: 15, fontFamily: FONT, outline: 'none',
};
const eye: React.CSSProperties = {
  position: 'absolute', right: 6, top: 6, height: 32, padding: '0 10px', borderRadius: 8, border: 'none',
  background: 'none', color: 'rgba(255,255,255,0.7)', fontSize: 13, fontFamily: FONT, cursor: 'pointer',
};
const actions: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12, marginTop: 18, flexWrap: 'wrap' };
const primary: React.CSSProperties = {
  minHeight: 44, padding: '0 22px', borderRadius: 10, border: 'none', background: LUMA_BLUE, color: '#fff',
  fontSize: 15, fontWeight: 600, fontFamily: FONT, cursor: 'pointer',
};
const ghostBtn: React.CSSProperties = {
  minHeight: 44, padding: '0 18px', borderRadius: 10, border: '1px solid rgba(255,255,255,0.12)',
  background: 'rgba(255,255,255,0.06)', color: 'rgba(255,255,255,0.95)', fontSize: 15, fontFamily: FONT, cursor: 'pointer',
};
const dangerBtn: React.CSSProperties = {
  minHeight: 44, padding: '0 8px', border: 'none', background: 'none', color: '#ef4444', fontSize: 15,
  fontFamily: FONT, cursor: 'pointer', marginLeft: 'auto',
};
const dialogBack: React.CSSProperties = {
  position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.5)', display: 'flex',
  alignItems: 'center', justifyContent: 'center', padding: 20,
};
const listCard: React.CSSProperties = {
  borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden', fontFamily: FONT,
};
const listRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 14, width: '100%', minHeight: 60, padding: '10px 16px 10px 12px', boxSizing: 'border-box',
  background: 'none', border: 'none', cursor: 'pointer', fontFamily: FONT, color: 'inherit',
};
const dialog: React.CSSProperties = {
  width: '100%', maxWidth: 400, borderRadius: 20, padding: 24, background: '#15102a', color: '#fff',
  border: '1px solid rgba(255,255,255,0.08)', fontFamily: FONT,
};
