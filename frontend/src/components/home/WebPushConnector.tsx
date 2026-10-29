/** Settings > Notifications > Web Push (owner only). Settings premium pass: one group —
 * Push notifications toggle (saves at once), This browser (On / Turn on / Blocked) and Send test.
 * No Contact Email: the client never sends vapid_email (the server keeps its default subject).
 * Same notifications.web_push.* keys as prod Services. No helper text. */
import { useEffect, useRef, useState } from 'react';
import type React from 'react';
import { apiFetch } from '@/apiClient';
import { FONT } from '@/components/start/StartUI';
import { T } from '@/theme/tokens';
import { usePushSubscription } from '@/hooks/usePushSubscription';

const API = '/api/settings/connectors/webpush';

type View = { enabled: boolean };

async function call<T>(url: string, init?: RequestInit): Promise<{ ok: boolean; status: number; data: T }> {
  try {
    const res = await apiFetch(url, init);
    return { ok: res.ok, status: res.status, data: (await res.json().catch(() => ({}))) as T };
  } catch {
    return { ok: false, status: 0, data: { error: "Couldn't reach Luma" } as T };
  }
}
const send = <T,>(url: string, method: string, body: unknown) =>
  call<T>(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export function WebPushConnector({ onSaved }: { onSaved: (text: string) => void }) {
  const [view, setView] = useState<View | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      const r = await call<View>(API);
      if (alive && r.ok) setView({ enabled: !!r.data.enabled });
    })();
    return () => { alive = false; };
  }, []);
  if (!view) return null;
  return <WebPushForm view={view} onSaved={(v) => { setView(v); onSaved('Saved'); }} />;
}

function WebPushForm({ view, onSaved }: { view: View; onSaved: (v: View) => void }) {
  const [enabled, setEnabled] = useState(!!view.enabled);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const alive = useRef(true);
  const { state, subscribe } = usePushSubscription({ anyLayout: true });
  useEffect(() => () => { alive.current = false; }, []);
  useEffect(() => {
    if (!result) return;
    const t = window.setTimeout(() => setResult(null), 4000);
    return () => window.clearTimeout(t);
  }, [result]);

  const subBusy = state === 'loading';
  const subDone = state === 'subscribed';
  const subDenied = state === 'denied';
  const subUnsupported = state === 'unsupported';

  /** The toggle saves at once; a failed save flips it back. */
  const toggle = async (on: boolean) => {
    if (saving) return;
    setEnabled(on);
    setSaving(true);
    setResult(null);
    const r = await send<View & { error?: string }>(API, 'PUT', { enabled: on });
    if (!alive.current) return;
    setSaving(false);
    if (r.ok && typeof r.data.enabled === 'boolean') { setEnabled(r.data.enabled); onSaved({ enabled: r.data.enabled }); return; }
    setEnabled(!on);
    setResult({ ok: false, text: r.data.error || "Couldn't save" });
  };

  const test = async () => {
    setTesting(true);
    setResult(null);
    const r = await send<{ ok?: boolean; message?: string; error?: string }>(`${API}/test`, 'POST', {});
    if (!alive.current) return;
    setTesting(false);
    setResult(r.data.ok
      ? { ok: true, text: r.data.message || 'Sent' }
      : { ok: false, text: r.data.error || 'Unexpected response' });
  };

  return (
    <div data-testid="connector-webpush">
      <div style={card}>
        <div style={rowStyle}>
          <span style={rowLabel}>Push notifications</span>
          <Toggle on={enabled} disabled={saving} onChange={on => { void toggle(on); }} />
        </div>
        <div style={{ ...rowStyle, borderTop: `1px solid ${T.glass8}` }}>
          <span style={rowLabel}>This browser</span>
          {subDone ? (
            <span style={chip(true)} data-testid="webpush-browser-on"><span style={dotS(true)} aria-hidden="true" />On</span>
          ) : subDenied || subUnsupported ? (
            <span style={chip(false)} data-testid="webpush-browser-blocked">{subDenied ? 'Blocked' : 'Unavailable'}</span>
          ) : (
            <button type="button" style={{ ...pill, opacity: subBusy ? 0.5 : 1 }} disabled={subBusy}
              onClick={() => { void subscribe(); }} data-testid="webpush-enable-notifications">
              {subBusy ? 'Turning on…' : 'Turn on'}
            </button>
          )}
        </div>
      </div>
      <div style={actions}>
        <span role="status" aria-live="polite" data-testid="webpush-result"
          style={{ fontSize: 13, minWidth: 0, overflowWrap: 'anywhere', flex: 1, textAlign: 'right',
            color: result ? (result.ok ? T.success : T.dangerText) : 'transparent' }}>
          {result?.text}
        </span>
        <button type="button" style={pill} disabled={testing}
          aria-busy={testing} onClick={test} data-testid="webpush-test">
          {testing ? 'Sending…' : 'Send test'}
        </button>
      </div>
    </div>
  );
}

/** 51×31 switch (same look as Settings switches); role=switch, saves on change. */
function Toggle({ on, disabled, onChange }: { on: boolean; disabled?: boolean; onChange: (on: boolean) => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label="Push notifications" disabled={disabled}
      data-testid="webpush-enabled" onClick={() => onChange(!on)}
      style={{ width: 59, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'flex-end', border: 'none',
        background: 'none', padding: 0, cursor: disabled ? 'default' : 'pointer', flex: 'none' }}>
      <span style={{ position: 'relative', display: 'block', width: 51, height: 31, borderRadius: 16,
        background: on ? '#3b82f6' : 'rgba(255,255,255,0.18)', transition: 'background .2s' }}>
        <span style={{ position: 'absolute', top: 2, left: 2, width: 27, height: 27, borderRadius: 14, background: '#fff',
          transition: 'transform .2s', transform: on ? 'translateX(20px)' : 'none', boxShadow: '0 1px 3px rgba(0,0,0,0.3)' }} />
      </span>
    </button>
  );
}

const card: React.CSSProperties = {
  borderRadius: 16, background: T.glass, border: `1px solid ${T.glass8}`, fontFamily: FONT, overflow: 'hidden',
};
const rowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 12, minHeight: 60, padding: '0 16px 0 20px', boxSizing: 'border-box',
};
const rowLabel: React.CSSProperties = { flex: 1, fontSize: 16, color: T.white, fontFamily: FONT };
const chip = (on: boolean): React.CSSProperties => ({
  height: 30, padding: '0 12px', borderRadius: 999, display: 'inline-flex', alignItems: 'center', gap: 7,
  fontSize: 13, fontWeight: 600, fontFamily: FONT,
  background: on ? 'rgba(34,197,94,0.14)' : 'rgba(255,255,255,0.08)', color: on ? '#86efac' : T.text3,
});
const dotS = (on: boolean): React.CSSProperties => ({
  width: 7, height: 7, borderRadius: 4, background: on ? T.success : T.text4,
});
const actions: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12, marginTop: 14 };
const pill: React.CSSProperties = {
  minHeight: 40, padding: '0 20px', borderRadius: 999, border: `1px solid ${T.glass12}`,
  background: 'rgba(255,255,255,0.08)', color: 'rgba(255,255,255,0.95)', fontSize: 14, fontWeight: 600,
  fontFamily: FONT, cursor: 'pointer', flex: 'none',
};
