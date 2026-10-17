/** Settings > Connectors (owner only): the TMDB API key and the Trakt client
 * ID. Both stay on the server; the browser only learns whether one is saved
 * and its last 4. */
import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '@/apiClient';
import { FONT } from '@/components/start/StartUI';
import { LUMA_BLUE } from '@/ui/lumaButton';
import { NoticeToast } from '@/components/start/SetupToast';
import { useKioskWidth } from '@/components/home/KioskHome';

type Masked = { set: boolean; last4: string };
interface View { tmdb: Masked; trakt?: Masked }
type Id = 'tmdb' | 'trakt';

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

const GROUPS: { id: Id; title: string; field: string; body: string; removeTitle: string }[] = [
  { id: 'tmdb', title: 'TMDB', field: 'API key', body: 'api_key', removeTitle: 'Remove TMDB API key?' },
  { id: 'trakt', title: 'Trakt', field: 'Client ID', body: 'client_id', removeTitle: 'Remove Trakt client ID?' },
];

export function ConnectorsSettings() {
  const [view, setView] = useState<View | null>(null);
  const [toast, setToast] = useState('');
  const wide = useKioskWidth();

  const load = useCallback(async () => {
    const r = await call<View>('/api/settings/connectors');
    if (r.ok) setView(r.data);
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!view) return null;
  return (
    <section data-testid="connectors-section" style={{ padding: wide ? 0 : '0 20px', display: 'flex', flexDirection: 'column', gap: 28 }}>
      {toast && <NoticeToast key={toast} text={toast} onDone={() => setToast('')} />}
      {GROUPS.map(g => (
        <ConnectorGroup key={g.id} group={g} masked={view[g.id] || { set: false, last4: '' }}
          onSaved={(v, removed) => { setView(v); setToast(removed ? 'Removed' : 'Saved'); }} />
      ))}
    </section>
  );
}

function ConnectorGroup({ group, masked, onSaved }: {
  group: (typeof GROUPS)[number]; masked: Masked; onSaved: (v: View, removed: boolean) => void;
}) {
  const { id } = group;
  const [key, setKey] = useState('');
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

  const test = async () => {
    setTesting(true);
    const r = await send<{ ok?: boolean; message?: string; error?: string }>(
      `/api/settings/connectors/${id}/test`, 'POST', trimmed ? { [group.body]: trimmed } : {});
    setTesting(false);
    setResult(r.data.ok ? { ok: true, text: 'Connected' } : { ok: false, text: r.data.error || 'Not connected' });
  };

  const save = async (value: string) => {
    setSaving(true);
    const r = await send<View & { error?: string }>(`/api/settings/connectors/${id}`, 'PUT', { [group.body]: value });
    setSaving(false);
    setConfirmRemove(false);
    if (!r.ok) { setResult({ ok: false, text: r.data.error || "Couldn't save" }); return; }
    setKey('');
    setShow(false);
    onSaved(r.data, !value);
  };

  return (
    <div data-testid={`connector-${id}`}>
      <h2 style={groupHeader}>{group.title}</h2>
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
        <div style={actions}>
          <button type="button" style={ghostBtn} disabled={testing || (!trimmed && !saved)} onClick={test} data-testid={`${id}-test`}>
            {testing ? 'Testing…' : 'Test'}
          </button>
          <button type="button" style={{ ...primary, opacity: trimmed && !saving ? 1 : 0.5 }}
                  disabled={!trimmed || saving} onClick={() => save(trimmed)} data-testid={`${id}-save`}>
            Save
          </button>
          {saved && !trimmed && (
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
        <div role="dialog" aria-modal="true" style={dialogBack} onClick={() => setConfirmRemove(false)}>
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
  display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600, color: 'rgba(255,255,255,0.7)',
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
const dialog: React.CSSProperties = {
  width: '100%', maxWidth: 400, borderRadius: 20, padding: 24, background: '#15102a', color: '#fff',
  border: '1px solid rgba(255,255,255,0.08)', fontFamily: FONT,
};
