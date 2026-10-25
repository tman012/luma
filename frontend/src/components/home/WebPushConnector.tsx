/** Settings > Notifications > Web Push (owner only): Enable Push Sending, Contact Email,
 * Enable Notifications (permission + /api/push/subscribe), Test and Save.
 * Same notifications.web_push.* keys as prod Services. No helper text. */
import { useEffect, useRef, useState } from 'react';
import type React from 'react';
import { apiFetch } from '@/apiClient';
import { FONT } from '@/components/start/StartUI';
import { LUMA_BLUE } from '@/ui/lumaButton';
import { T } from '@/theme/tokens';
import { usePushSubscription } from '@/hooks/usePushSubscription';

const API = '/api/settings/connectors/webpush';

type View = { enabled: boolean; vapid_email: string };

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
      if (alive && r.ok) setView(r.data);
    })();
    return () => { alive = false; };
  }, []);
  if (!view) return null;
  return <WebPushForm key={`${view.enabled}:${view.vapid_email}`} view={view}
    onSaved={(v) => { setView(v); onSaved('Saved'); }} />;
}

function WebPushForm({ view, onSaved }: { view: View; onSaved: (v: View) => void }) {
  const [enabled, setEnabled] = useState(!!view.enabled);
  const [email, setEmail] = useState(view.vapid_email || '');
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

  const dirty = enabled !== !!view.enabled || email.trim() !== (view.vapid_email || '');
  const canSave = dirty && !saving;
  const subBusy = state === 'loading';
  const subDone = state === 'subscribed';
  const subDenied = state === 'denied';
  const subUnsupported = state === 'unsupported';
  const enableLabel = subBusy ? 'Enabling…' : subDone ? 'Enabled' : subDenied ? 'Blocked' : 'Enable Notifications';

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

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    setResult(null);
    const r = await send<View & { error?: string }>(API, 'PUT', {
      enabled, vapid_email: email.trim(),
    });
    if (!alive.current) return;
    setSaving(false);
    if (r.ok && typeof r.data.enabled === 'boolean') {
      onSaved({ enabled: r.data.enabled, vapid_email: r.data.vapid_email || '' });
      return;
    }
    setResult({ ok: false, text: r.data.error || "Couldn't save" });
  };

  return (
    <div data-testid="connector-webpush">
      <h2 style={{ ...groupHeader, display: 'flex', alignItems: 'center', gap: 8 }}>
        Web Push Notifications
        <span style={dot(enabled)} aria-hidden="true" data-testid="webpush-status" />
      </h2>
      <form style={card} noValidate onSubmit={e => { e.preventDefault(); save(); }}>
        <label style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
          <input
            type="checkbox" data-testid="webpush-enabled"
            checked={enabled}
            onChange={e => setEnabled(e.target.checked)}
            style={{ width: 18, height: 18 }}
          />
          Enable Push Sending
        </label>
        <div>
          <label htmlFor="webpush-email" style={labelStyle}>Contact Email</label>
          <input
            id="webpush-email" data-testid="webpush-email"
            style={inputStyle}
            type="email" autoComplete="email" spellCheck={false}
            placeholder="mailto:you@example.com"
            value={email} onChange={e => setEmail(e.target.value)}
          />
        </div>
        <button
          type="button"
          style={{ ...primary, opacity: subBusy || subDone || subDenied || subUnsupported ? 0.5 : 1, alignSelf: 'flex-start' }}
          disabled={subBusy || subDone || subDenied || subUnsupported}
          onClick={() => { void subscribe(); }}
          data-testid="webpush-enable-notifications"
        >
          {enableLabel}
        </button>
        <div style={actions}>
          <button type="button" style={ghostBtn} disabled={testing}
            aria-busy={testing} onClick={test} data-testid="webpush-test">
            {testing ? 'Testing…' : 'Test'}
          </button>
          <button type="submit" style={{ ...primary, opacity: canSave ? 1 : 0.4 }}
            disabled={!canSave} data-testid="webpush-save">Save</button>
          <span role="status" aria-live="polite" data-testid="webpush-result"
            style={{ fontSize: 13, minWidth: 0, overflowWrap: 'anywhere',
              color: result ? (result.ok ? T.success : T.dangerText) : 'transparent' }}>
            {result?.text}
          </span>
        </div>
      </form>
    </div>
  );
}

const groupHeader: React.CSSProperties = {
  margin: '0 4px 8px', fontSize: 13, fontWeight: 600, color: T.text3, fontFamily: FONT,
};
const card: React.CSSProperties = {
  borderRadius: 16, padding: 20, background: T.glass,
  border: `1px solid ${T.glass8}`, display: 'flex', flexDirection: 'column', gap: 16, fontFamily: FONT, margin: 0,
};
const labelStyle: React.CSSProperties = {
  display: 'block', margin: '0 0 6px', fontSize: 13, fontWeight: 500, color: T.text2, fontFamily: FONT,
};
const dot = (on: boolean): React.CSSProperties => ({
  width: 8, height: 8, borderRadius: 4, background: on ? T.success : T.text4,
});
const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', height: 44, padding: '0 14px', borderRadius: 10,
  background: T.glass, border: `1px solid ${T.glass12}`, color: T.white,
  fontSize: 16, fontFamily: FONT, outline: 'none',
};
const actions: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12, marginTop: 2, flexWrap: 'wrap' };
const primary: React.CSSProperties = {
  minHeight: 44, padding: '0 22px', borderRadius: 10, border: 'none', background: LUMA_BLUE, color: '#fff',
  fontSize: 15, fontWeight: 600, fontFamily: FONT, cursor: 'pointer',
};
const ghostBtn: React.CSSProperties = {
  minHeight: 44, padding: '0 18px', borderRadius: 10, border: '1px solid rgba(255,255,255,0.12)',
  background: 'rgba(255,255,255,0.06)', color: 'rgba(255,255,255,0.95)', fontSize: 15, fontFamily: FONT, cursor: 'pointer',
};
