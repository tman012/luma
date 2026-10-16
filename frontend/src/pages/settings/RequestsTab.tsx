/**
 * RequestsTab — the friends & family request page (/request).
 * Uses /api/request/admin/settings (the password is write-only and stored hashed).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ToggleSwitch } from './components/ToggleSwitch';

interface ReqSettings {
  enabled: boolean;
  has_password: boolean;
  movie_profile: string;
  tv_profile: string;
  exclude_libraries: string[];
  radarr_profiles?: string[];
  sonarr_profiles?: string[];
  libraries?: string[];
  public_url?: string;
}

interface LogEntry {
  id: string;
  ts: number;
  who: string;
  kind: 'movie' | 'tv';
  title: string;
  scope: string;
  season?: number | null;
  episodes?: number[] | null;
  action: string;
}

const inputClass =
  'w-full min-h-[44px] rounded-lg border border-white/10 bg-white/5 px-3 py-3 text-[15px] text-white placeholder:text-white/25 outline-none transition-colors focus:border-white/20 focus:bg-white/8';
const labelClass = 'text-[14px] font-medium text-white/60';
const card = 'rounded-2xl border border-white/10 bg-white/[0.04] p-4 sm:p-6';

function Card({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className={card}>
      <div className="mb-4 flex items-center gap-3">
        <div className="flex h-[38px] w-[38px] sm:h-[42px] sm:w-[42px] items-center justify-center rounded-[10px] bg-white/5 text-white/50 shrink-0">
          {icon}
        </div>
        <h3 className="text-[15px] font-semibold text-white/90">{title}</h3>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">{children}</div>
    </div>
  );
}

function describe(e: LogEntry): string {
  if (e.kind === 'movie') return e.title;
  if (e.scope === 'show') return `${e.title} · All Seasons`;
  if (e.scope === 'season') return `${e.title} · Season ${e.season}`;
  const eps = (e.episodes || []).map((n) => `E${n}`).join(', ');
  return `${e.title} · S${e.season} ${eps}`;
}

function when(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export function RequestsTab() {
  const [saved, setSaved] = useState<ReqSettings | null>(null);
  const [draft, setDraft] = useState<ReqSettings | null>(null);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    const r = await fetch('/api/request/admin/settings');
    if (!r.ok) return;
    const d: ReqSettings = await r.json();
    setSaved(d);
    setDraft(d);
    fetch('/api/request/admin/log').then((x) => (x.ok ? x.json() : [])).then((l) => setLog(Array.isArray(l) ? l.slice(0, 12) : []));
  }, []);

  useEffect(() => { void load(); }, [load]);

  const dirty = useMemo(() => {
    if (!saved || !draft) return false;
    return password.length > 0 || saved.enabled !== draft.enabled || saved.movie_profile !== draft.movie_profile
      || saved.tv_profile !== draft.tv_profile
      || [...saved.exclude_libraries].sort().join('|') !== [...draft.exclude_libraries].sort().join('|');
  }, [saved, draft, password]);

  if (!draft) {
    return <div className="py-10 text-center text-[13px] text-white/40">Loading…</div>;
  }

  const set = (p: Partial<ReqSettings>) => setDraft({ ...draft, ...p });
  const url = draft.public_url || `${window.location.origin}/request`;
  const libs = Array.from(new Set([...(draft.libraries || []), ...draft.exclude_libraries]));

  const save = async () => {
    if (password && password.length < 4) { setStatus({ ok: false, text: 'Password must be at least 4 characters' }); return; }
    setBusy(true); setStatus(null);
    try {
      const body: Record<string, unknown> = {
        enabled: draft.enabled, movie_profile: draft.movie_profile, tv_profile: draft.tv_profile,
        exclude_libraries: draft.exclude_libraries,
      };
      if (password) body.password = password;
      const r = await fetch('/api/request/admin/settings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error();
      const d: ReqSettings = await r.json();
      setSaved(d); setDraft(d); setPassword('');
      setStatus({ ok: true, text: 'Saved' });
    } catch {
      setStatus({ ok: false, text: 'Couldn’t save' });
    } finally { setBusy(false); }
  };

  const copy = async () => {
    try { await navigator.clipboard.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ }
  };

  return (
    <div className="space-y-6">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">Requests</h2>
      </div>

      <Card
        title="Request Page"
        icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></svg>}
      >
        <div className="sm:col-span-2">
          <ToggleSwitch label="Enabled" path="requests.enabled" value={draft.enabled} onChange={(v) => set({ enabled: v })} />
        </div>
        <div className="sm:col-span-2 flex flex-col gap-1.5">
          <label className={labelClass}>Link</label>
          <div className="flex gap-2">
            <input className={`${inputClass} font-mono text-[13px] text-white/70`} value={url} readOnly onFocus={(e) => e.currentTarget.select()} />
            <button type="button" onClick={copy}
              className="shrink-0 rounded-lg border border-white/10 bg-white/[0.06] px-4 text-[13px] font-semibold text-white/70 hover:bg-white/10 min-h-[44px]">
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>
        <div className="sm:col-span-2 flex flex-col gap-1.5">
          <label className={labelClass}>Password</label>
          <input className={inputClass} type="password" autoComplete="new-password" value={password}
            placeholder={draft.has_password ? '••••••••' : 'Set a password'} onChange={(e) => setPassword(e.target.value)} />
        </div>
      </Card>

      <Card
        title="Quality Profiles"
        icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" /><line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" /><line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" /><line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" /></svg>}
      >
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>Movies</label>
          <select className={`${inputClass} cursor-pointer`} value={draft.movie_profile} onChange={(e) => set({ movie_profile: e.target.value })}>
            <option value="" className="bg-kiosk-black text-white">Default</option>
            {(draft.radarr_profiles || []).map((p) => <option key={p} value={p} className="bg-kiosk-black text-white">{p}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className={labelClass}>TV</label>
          <select className={`${inputClass} cursor-pointer`} value={draft.tv_profile} onChange={(e) => set({ tv_profile: e.target.value })}>
            <option value="" className="bg-kiosk-black text-white">Default</option>
            {(draft.sonarr_profiles || []).map((p) => <option key={p} value={p} className="bg-kiosk-black text-white">{p}</option>)}
          </select>
        </div>
      </Card>

      <Card
        title="Excluded Libraries"
        icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="18" rx="1" /><rect x="14" y="3" width="7" height="18" rx="1" /></svg>}
      >
        <div className="col-span-full flex flex-wrap gap-1.5">
          {libs.map((name) => {
            const on = draft.exclude_libraries.includes(name);
            return (
              <button key={name} type="button"
                onClick={() => set({ exclude_libraries: on ? draft.exclude_libraries.filter((n) => n !== name) : [...draft.exclude_libraries, name] })}
                className={`min-h-[40px] rounded-lg border px-3 py-2 text-[13px] font-medium transition-colors ${
                  on ? 'border-emerald-400/30 bg-emerald-400/15 text-emerald-200' : 'border-white/10 bg-white/5 text-white/45'}`}>
                {name}
              </button>
            );
          })}
        </div>
      </Card>

      <div className="flex items-center justify-end gap-3">
        {status && <span className={`text-[13px] ${status.ok ? 'text-emerald-400' : 'text-red-400'}`}>{status.text}</span>}
        <button type="button" onClick={() => { setDraft(saved); setPassword(''); setStatus(null); }} disabled={!dirty || busy}
          className="inline-flex items-center justify-center rounded-xl border border-white/10 bg-white/[0.06] px-4 py-2.5 text-[13px] font-semibold text-white/60 hover:bg-white/10 disabled:opacity-30 min-h-[44px]">
          Reset
        </button>
        <button type="button" onClick={save} disabled={!dirty || busy}
          className="inline-flex items-center justify-center rounded-xl bg-emerald-500 px-5 py-2.5 text-[13px] font-semibold text-white hover:bg-emerald-600 disabled:opacity-40 min-h-[44px]">
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>

      {log.length > 0 && (
        <Card
          title="Recent Requests"
          icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><polyline points="12 7 12 12 15 14" /></svg>}
        >
          <div className="col-span-full -my-1 divide-y divide-white/[0.06]">
            {log.map((e) => (
              <div key={e.id} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <div className="truncate text-[14px] text-white/85">{describe(e)}</div>
                  <div className="text-[12px] text-white/40">{e.who}</div>
                </div>
                <div className="shrink-0 text-[12px] text-white/40">{when(e.ts)}</div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
