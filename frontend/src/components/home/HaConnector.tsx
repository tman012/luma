/** Settings > Connectors > Home Assistant (owner only): URL, Access Token,
 * Test and Save. Pre-filled from the server's homeassistant settings (the
 * section Rooms power and the smart-home panel use). The access token never
 * comes back: a saved one shows only as dots (and last 4 in the placeholder)
 * and is sent again only when retyped. */
import { useEffect, useRef, useState } from 'react';
import type React from 'react';
import { apiFetch } from '@/apiClient';
import { FONT } from '@/components/start/StartUI';
import { T } from '@/theme/tokens';
import { LUMA_BLUE } from '@/ui/lumaButton';

export const HA_API = '/api/settings/connectors/homeassistant';
export const HA_UNREACHABLE = "Couldn't reach Luma";

export interface HaView {
  url: string;
  access_token: { set: boolean; last4: string };
}
type Fields = Partial<Record<'url' | 'access_token', string>>;
interface Answer { ok?: boolean; message?: string; error?: string; fields?: Fields }

async function call<T>(url: string, init?: RequestInit): Promise<{ ok: boolean; status: number; data: T }> {
  try {
    const res = await apiFetch(url, init);
    return { ok: res.ok, status: res.status, data: (await res.json().catch(() => ({}))) as T };
  } catch {
    return { ok: false, status: 0, data: { error: HA_UNREACHABLE } as T };
  }
}
const send = <T,>(url: string, method: string, body: unknown) =>
  call<T>(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

const normUrl = (u: string) => u.trim().replace(/\/+$/, '');

export function HaConnector({ onSaved }: { onSaved: (text: string) => void }) {
  const [view, setView] = useState<HaView | null>(null);
  useEffect(() => {
    let alive = true;
    call<HaView>(HA_API).then(r => {
      if (alive && r.ok && r.data && typeof r.data.url === 'string') setView(r.data);
    });
    return () => { alive = false; };
  }, []);
  if (!view) return null;
  return <HaForm key={JSON.stringify(view)} view={view} onSaved={(v, removed) => {
    setView(v);
    onSaved(removed ? 'Removed' : 'Saved');
  }} />;
}

function HaForm({ view, onSaved }: {
  view: HaView;
  onSaved: (v: HaView, removed: boolean) => void;
}) {
  const [url, setUrl] = useState(view.url);
  const [token, setToken] = useState('');
  const [show, setShow] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [errors, setErrors] = useState<Fields>({});
  const [confirmRemove, setConfirmRemove] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  useEffect(() => {
    if (!result?.ok) return;
    const t = window.setTimeout(() => setResult(null), 4000);
    return () => window.clearTimeout(t);
  }, [result]);

  const urlChanged = normUrl(url) !== normUrl(view.url);
  const keepsToken = view.access_token.set && !urlChanged;
  const dirty = urlChanged || token !== '';
  const canSave = !!url.trim() && dirty && !saving;
  const saved = !!view.url && view.access_token.set;

  const edit = (key: keyof Fields, set: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    set(e.target.value);
    setErrors(x => {
      if (!x[key] && !(key === 'url' && x.access_token)) return x;
      const n = { ...x };
      delete n[key];
      if (key === 'url') delete n.access_token;
      return n;
    });
    setResult(r => (r && !r.ok ? null : r));
    setConfirmRemove(false);
  };

  const body = () => ({
    url: url.trim(),
    ...(token !== '' ? { access_token: token } : null),
  });

  const test = async () => {
    if (testing) return;
    if (!url.trim()) { setErrors(e => ({ ...e, url: 'Required' })); return; }
    setTesting(true);
    setResult(null);
    const r = await send<Answer>(`${HA_API}/test`, 'POST', body());
    if (!alive.current) return;
    setTesting(false);
    if (r.data.fields) setErrors(r.data.fields);
    if (r.ok && r.data.ok) {
      setResult({ ok: true, text: 'Connected' });
    } else {
      setResult({ ok: false, text: r.data.error || (r.status === 0 ? HA_UNREACHABLE : 'Unexpected response') });
    }
  };

  const save = async (clear = false) => {
    if (clear) {
      setSaving(true);
      const r = await send<HaView & Answer>(HA_API, 'PUT', { url: '', access_token: '' });
      if (!alive.current) return;
      setSaving(false);
      setConfirmRemove(false);
      if (r.ok && typeof r.data.url === 'string') { onSaved(r.data, true); return; }
      setResult({ ok: false, text: r.data.error || (r.status === 0 ? HA_UNREACHABLE : "Couldn't save") });
      return;
    }
    if (!canSave) { if (!url.trim()) setErrors(e => ({ ...e, url: 'Required' })); return; }
    setSaving(true);
    const r = await send<HaView & Answer>(HA_API, 'PUT', body());
    if (!alive.current) return;
    setSaving(false);
    if (r.ok && typeof r.data.url === 'string') {
      setToken('');
      setShow(false);
      onSaved(r.data, false);
      return;
    }
    if (r.data.fields) setErrors(r.data.fields);
    else setResult({ ok: false, text: r.data.error || (r.status === 0 ? HA_UNREACHABLE : "Couldn't save") });
  };

  const tokenPlaceholder = keepsToken
    ? (view.access_token.last4 ? `••••${view.access_token.last4}` : '••••••••')
    : '';

  return (
    <div data-testid="connector-homeassistant">
      <h2 style={{ ...groupHeader, display: 'flex', alignItems: 'center', gap: 8 }}>
        Home Assistant<span style={dot(saved)} aria-hidden="true" data-testid="ha-status" />
      </h2>
      <form style={card} noValidate onSubmit={e => { e.preventDefault(); save(); }}>
        <div>
          <label htmlFor="ha-url" style={labelStyle}>URL</label>
          <input
            id="ha-url" data-testid="ha-url"
            style={{ ...inputStyle, ...(errors.url ? invalid : null) }}
            type="url" inputMode="url" autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false}
            aria-invalid={errors.url ? true : undefined}
            aria-describedby={errors.url ? 'ha-url-error' : undefined}
            placeholder="http://homeassistant.local:8123"
            value={url} onChange={edit('url', setUrl)}
          />
          {errors.url && <span id="ha-url-error" data-testid="ha-url-error" style={fieldError}>{errors.url}</span>}
        </div>
        <div>
          <label htmlFor="ha-access-token" style={labelStyle}>Access Token</label>
          <div style={{ position: 'relative' }}>
            <input
              id="ha-access-token" data-testid="ha-access-token" data-secret="true"
              style={{ ...inputStyle, paddingRight: 64, ...(errors.access_token ? invalid : null) }}
              type={show ? 'text' : 'password'} autoComplete="new-password" autoCapitalize="off" spellCheck={false}
              aria-invalid={errors.access_token ? true : undefined}
              aria-describedby={errors.access_token ? 'ha-access-token-error' : undefined}
              placeholder={tokenPlaceholder}
              value={token} onChange={edit('access_token', setToken)}
            />
            <button type="button" style={eye} aria-label={show ? 'Hide' : 'Show'} aria-pressed={show}
              onClick={() => setShow(s => !s)}>{show ? 'Hide' : 'Show'}</button>
          </div>
          {errors.access_token && (
            <span id="ha-access-token-error" data-testid="ha-access-token-error" style={fieldError}>
              {errors.access_token}
            </span>
          )}
        </div>
        <div style={actions}>
          <button type="button" style={ghostBtn} disabled={testing || (!url.trim() && !saved)}
            aria-busy={testing} onClick={test} data-testid="ha-test">
            {testing ? 'Testing…' : 'Test'}
          </button>
          <button type="submit" style={{ ...primary, opacity: canSave ? 1 : 0.4 }}
            disabled={!canSave} data-testid="ha-save">Save</button>
          {saved && !dirty && (
            <button type="button" style={dangerBtn} onClick={() => setConfirmRemove(true)} data-testid="ha-remove">
              Remove
            </button>
          )}
          <span role="status" aria-live="polite" data-testid="ha-result"
            style={{ fontSize: 13, minWidth: 0, overflowWrap: 'anywhere',
              color: result ? (result.ok ? T.success : T.dangerText) : 'transparent' }}>
            {result?.text}
          </span>
        </div>
      </form>
      {confirmRemove && saved && !dirty && (
        <div role="dialog" aria-modal="true" className="luma-bleed" style={dialogBack}
          onClick={() => setConfirmRemove(false)}>
          <div style={dialog} onClick={e => e.stopPropagation()}>
            <p style={{ margin: '0 0 20px', fontSize: 17, fontWeight: 600 }}>Remove Home Assistant?</p>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
              <button type="button" style={ghostBtn} onClick={() => setConfirmRemove(false)}>Cancel</button>
              <button type="button" style={{ ...primary, background: '#ef4444' }}
                onClick={() => save(true)} data-testid="ha-remove-confirm">Remove</button>
            </div>
          </div>
        </div>
      )}
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
const invalid: React.CSSProperties = { borderColor: T.dangerText };
const fieldError: React.CSSProperties = {
  display: 'block', marginTop: 6, fontSize: 12, color: T.dangerText, fontFamily: FONT,
};
const eye: React.CSSProperties = {
  position: 'absolute', right: 6, top: 6, height: 32, padding: '0 10px', borderRadius: 8, border: 'none',
  background: 'none', color: 'rgba(255,255,255,0.7)', fontSize: 13, fontFamily: FONT, cursor: 'pointer',
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
