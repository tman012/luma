/** v260 Settings > Automation > Downloads guard: prod 1b4ea74's DownloadsGuardCard on the test install. Keeps free
 * space above a floor by deleting finished downloads through qBittorrent (with their files), oldest first, from the
 * chosen trackers in order. Owner only; turning it on, widening what it may delete, Clean up now and the folder
 * need the signed-in owner. Settings save as they change. */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { FONT } from '@/components/start/StartUI';
import { Select, Toggle, SAVE_ERROR, field, glassBtn, tvEditCss } from '@/components/library/TvEdit';
import { formatBytes } from '@/services/storageApi';
import { Field, NumField, grCss, when } from './GenreRouletteSettings';
import {
  loadDg, loadDgStatus, loadDgTrackers, previewDg, runDg, saveDg,
  type DgEntry, type DgLogEntry, type DgPreview, type DgResult, type DgSettings, type DgStatus,
  type DgTrackerSummary, type DgView, type Scope,
} from './downloadsGuardApi';

const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const RED = '#fca5a5';
const AMBER = '#fcd34d';
const GREEN = '#4ade80';
const panel: CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', boxSizing: 'border-box' };
const group: CSSProperties = { margin: '0 0 8px', fontSize: 11, fontWeight: 700, letterSpacing: '0.02em', color: '#a5b4fc' };
const tile: CSSProperties = { borderRadius: 12, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.06)', padding: '10px 12px', minWidth: 0 };
const pill: CSSProperties = { borderRadius: 20, height: 40, padding: '0 20px' };
const danger: CSSProperties = { background: 'rgba(248,113,113,0.16)', border: '1px solid rgba(248,113,113,0.4)', color: '#fecaca' };
const rowBtn: CSSProperties = { ...glassBtn, height: 30, width: 30, padding: 0, borderRadius: 15, fontSize: 14, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' };

export const SIGN_IN_TO_CHANGE = 'Sign in to make this change';
export const SCOPES: { id: Scope; label: string }[] = [
  { id: 'all', label: 'All finished downloads' },
  { id: 'test_tagged', label: "Only this install's downloads" },
];
/** Keep downloads newer than: days (prod's 48 hours is the 2-day default); 0 keeps none back. */
export const AGE_DAYS = [0, 1, 2, 3, 4, 5, 6, 7, 10, 14, 21, 30, 60, 90];
export const dayLabel = (n: number) => (n === 0 ? 'Off' : n === 1 ? '1 day' : `${n} days`);
export function ageOptions(cur: number): { id: number; label: string }[] {
  const days = AGE_DAYS.includes(cur) ? AGE_DAYS : [...AGE_DAYS, cur].sort((a, b) => a - b);
  return days.map(n => ({ id: n, label: dayLabel(n) }));
}
const gb = (n: number) => `${n.toLocaleString('en-US')} GB`;

function Switch({ id, label, on, onChange }: { id: string; label: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, height: 40 }}>
      <span id={`${id}-label`} style={{ fontSize: 13, color: W95 }}>{label}</span>
      <Toggle id={id} on={on} onChange={onChange} />
    </div>
  );
}

/** A setting that is off at 0: a toggle, and the number while it is on. */
function Optional({ id, label, value, fallback, decimals = 0, step = 1, bad, onChange }: {
  id: string; label: string; value: number; fallback: number; decimals?: number; step?: number; bad?: boolean; onChange: (v: number) => void;
}) {
  const on = value > 0;
  return (
    <div style={{ minWidth: 0 }}>
      <Switch id={id} label={label} on={on} onChange={v => onChange(v ? fallback : 0)} />
      {on && <NumField id={`${id}-value`} value={value} decimals={decimals} step={step} bad={bad} onCommit={v => onChange(v > 0 ? v : 0)} />}
    </div>
  );
}

/** Mirrors prod's describe() (downloads_guard.summarise). */
export function describe(r: DgResult): string {
  if (r.error) return r.error;
  if (!r.triggered) return r.reason || 'Nothing to do';
  if (!r.freed_bytes) return `Nothing deleted: ${r.reason}`;
  const parts = [`${r.dry_run ? 'Would free' : 'Freed'} ${formatBytes(r.freed_bytes)} across ${r.deleted.length} download${r.deleted.length === 1 ? '' : 's'}`];
  if (r.freed_fallback_bytes) parts.push(`${formatBytes(r.freed_preferred_bytes ?? 0)} from your trackers, ${formatBytes(r.freed_fallback_bytes)} from others`);
  if (r.short) parts.push('short of the target');
  if (r.failed.length) parts.push(`${r.failed.length} couldn't be deleted`);
  if (!r.dry_run && r.free_after_bytes != null) parts.push(`${formatBytes(r.free_after_bytes)} free now`);
  return parts.join(' · ');
}

function age(mtime?: number): string {
  if (!mtime) return '';
  const d = Math.floor((Date.now() / 1000 - mtime) / 86400);
  return d <= 0 ? 'today' : d === 1 ? '1 day' : `${d} days`;
}

function Entries({ title, entries, testid }: { title: string; entries: DgEntry[]; testid: string }) {
  if (!entries.length) return null;
  return (
    <section aria-label={title} data-testid={testid} style={{ ...panel, padding: '16px 16px 10px' }}>
      <h3 style={{ margin: '0 0 10px', fontSize: 16, fontWeight: 600, color: W95 }}>{title}</h3>
      <div role="list">
        {entries.slice(0, 40).map(e => (
          <div role="listitem" key={e.name} data-testid={`${testid}-row`}
            style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, minHeight: 32, borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 6 }}>
            <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: W95, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.name}</span>
            <span style={{ flex: 'none', fontSize: 12, color: W50 }}>
              <span style={{ color: e.phase === 'fallback' ? AMBER : W70 }}>{e.tracker_label || 'Unknown'}</span>
              {' · '}{formatBytes(e.size_bytes)}{e.mtime ? ` · ${age(e.mtime)}` : ''}
            </span>
          </div>
        ))}
        {entries.length > 40 && <div style={{ padding: '6px 0', fontSize: 12, color: W50 }}>{`${entries.length - 40} more`}</div>}
      </div>
    </section>
  );
}

function TrackerList({ order, known, summary, onChange }: {
  order: string[]; known: DgView['trackers']; summary: DgTrackerSummary | null; onChange: (next: string[]) => void;
}) {
  const label = (k: string) => known.find(t => t.key === k)?.label || k;
  const stat = (k: string) => summary?.trackers.find(t => t.key === k);
  const move = (i: number, d: number) => { const n = [...order]; const a = n[i]!; n[i] = n[i + d]!; n[i + d] = a; onChange(n); };
  const rest = known.filter(t => !order.includes(t.key));
  return (
    <div>
      <div role="list" aria-label="Trackers" data-testid="dg-trackers">
        {order.length === 0 && <div style={{ padding: '6px 0 10px', fontSize: 13, color: W50 }}>None</div>}
        {order.map((k, i) => {
          const s = stat(k);
          return (
            <div role="listitem" key={k} data-testid="dg-tracker-row"
              style={{ display: 'flex', alignItems: 'center', gap: 8, minHeight: 44, borderTop: i ? '1px solid rgba(255,255,255,0.06)' : undefined }}>
              <span style={{ width: 18, fontSize: 12, color: W50, textAlign: 'right' }}>{i + 1}</span>
              <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: W95 }}>{label(k)}
                {s ? <span style={{ color: W50 }}>{` · ${s.count} · ${formatBytes(s.size_bytes)}`}</span> : null}</span>
              <button type="button" className="tve-glass lp-focus" aria-label={`Move ${label(k)} up`} disabled={i === 0}
                data-testid={`dg-up-${k}`} onClick={() => move(i, -1)} style={{ ...rowBtn, opacity: i === 0 ? 0.35 : 1 }}>↑</button>
              <button type="button" className="tve-glass lp-focus" aria-label={`Move ${label(k)} down`} disabled={i === order.length - 1}
                data-testid={`dg-down-${k}`} onClick={() => move(i, 1)} style={{ ...rowBtn, opacity: i === order.length - 1 ? 0.35 : 1 }}>↓</button>
              <button type="button" className="tve-glass lp-focus" aria-label={`Remove ${label(k)}`}
                data-testid={`dg-remove-${k}`} onClick={() => onChange(order.filter(x => x !== k))} style={rowBtn}>×</button>
            </div>
          );
        })}
      </div>
      {rest.length > 0 && (
        <div role="group" aria-label="Add a tracker" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
          {rest.map(t => (
            <button key={t.key} type="button" className="gr-chip lp-focus" data-testid={`dg-add-${t.key}`} onClick={() => onChange([...order, t.key])}
              style={{ height: 32, padding: '0 10px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.14)', background: 'none', color: W95, fontSize: 13, fontFamily: FONT, cursor: 'pointer' }}>
              {`+ ${t.label}`}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Never delete: qBit tags (any case, exact) and torrent hashes. Adding is open to the owner; removing needs the
 * signed-in owner (the server answers 401 and the sign-in prompt shows). */
function NeverDelete({ tags, hashes, onTags, onHashes }: {
  tags: string[]; hashes: string[]; onTags: (next: string[]) => void; onHashes: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const t = draft.trim();
    setDraft('');
    if (!t || t.includes(',') || tags.some(x => x.toLowerCase() === t.toLowerCase())) return;
    onTags([...tags, t]);
  };
  const chip: CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 6px 0 10px', borderRadius: 6,
    border: '1px solid rgba(255,255,255,0.14)', fontSize: 13, color: W95 };
  const x = (label: string, onClick: () => void, testid: string) => (
    <button type="button" className="lp-focus" aria-label={label} data-testid={testid} onClick={onClick}
      style={{ width: 22, height: 22, border: 'none', borderRadius: 11, background: 'rgba(255,255,255,0.08)', color: W70, cursor: 'pointer', padding: 0 }}>×</button>
  );
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div role="list" aria-label="Never delete" data-testid="dg-never" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {tags.map(t => (
          <span role="listitem" key={`t-${t}`} style={chip} data-testid="dg-never-tag">{t}{x(`Remove ${t}`, () => onTags(tags.filter(v => v !== t)), `dg-never-remove-${t}`)}</span>
        ))}
        {hashes.map(h => (
          <span role="listitem" key={`h-${h}`} title={h} style={chip} data-testid="dg-never-hash">{`…${h.slice(-4)}`}{x(`Remove …${h.slice(-4)}`, () => onHashes(hashes.filter(v => v !== h)), `dg-never-remove-${h.slice(-4)}`)}</span>
        ))}
        {tags.length + hashes.length === 0 && <span style={{ fontSize: 13, color: W50 }}>None</span>}
      </div>
      <input className="tve-in" aria-label="Add a tag" placeholder="Add a tag" value={draft} data-testid="dg-never-add"
        onChange={e => setDraft(e.target.value)} onBlur={add} onKeyDown={e => { if (e.key === 'Enter') add(); }} style={field} />
    </div>
  );
}

function FolderField({ value, placeholder, bad, onCommit }: { value: string; placeholder: string; bad: boolean; onCommit: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => { const v = draft.trim(); if (v !== value) onCommit(v); };
  return (
    <input id="dg-folder" className="tve-in" value={draft} placeholder={placeholder} aria-invalid={bad || undefined} data-testid="dg-folder-input"
      onChange={e => setDraft(e.target.value)} onBlur={commit} onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
      style={{ ...field, ...(bad ? { borderColor: '#ef4444' } : null) }} />
  );
}

function StatusPanel({ status, allowed }: { status: DgStatus | null; allowed: boolean }) {
  const ev = status?.evaluation;
  const st = !allowed ? { text: 'Off on the test instance', color: RED }
    : status?.running ? { text: 'Running', color: '#60a5fa' }
      : !ev?.enabled ? { text: 'Off', color: W50 }
        : ev.available === false ? { text: "Can't read the folder", color: RED }
          : { text: 'Watching', color: GREEN };
  const r = status?.last_result;
  const tiles: [string, string][] = [
    ['Free space', ev?.available ? formatBytes(ev.free_bytes) : '—'],
    ['Deleted today', ev ? formatBytes(ev.freed_today_bytes) : '—'],
    ['Last check', status?.last_check ? when(new Date(status.last_check)) : '—'],
    ['Last cleanup', status?.last_run ? when(new Date(status.last_run)) : 'Never'],
  ];
  return (
    <section aria-label="Status" data-testid="dg-status" style={{ ...panel, padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, gap: 12 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: W95 }}>Status</h3>
        <span data-testid="dg-state" style={{ fontSize: 12, color: st.color, textAlign: 'right' }}>● {st.text}</span>
      </div>
      <div className="gr-f2" style={{ gap: 8 }}>
        {tiles.map(([k, v]) => (
          <div key={k} style={tile} data-testid="dg-tile">
            <div style={{ fontSize: 12, color: W70, marginBottom: 4 }}>{k}</div>
            <div style={{ fontSize: 15, color: W95, overflowWrap: 'anywhere' }}>{v}</div>
          </div>
        ))}
      </div>
      {r && <p data-testid="dg-last" style={{ margin: '12px 0 0', fontSize: 13, color: r.error ? RED : r.freed_bytes ? GREEN : W70 }}>{describe(r)}</p>}
      {r?.failed?.length ? <p style={{ margin: '4px 0 0', fontSize: 12, color: AMBER }}>{`Couldn't delete ${r.failed.slice(0, 5).join(', ')}`}</p> : null}
    </section>
  );
}

function Activity({ log }: { log: DgLogEntry[] }) {
  if (!log.length) return null;
  const tone = (o: string) => (o === 'error' ? RED : o === 'freed' ? GREEN : o === 'preview' ? '#93c5fd' : W70);
  return (
    <section aria-label="Recent activity" data-testid="dg-activity" style={{ ...panel, padding: '16px 16px 10px' }}>
      <h3 style={{ margin: '0 0 10px', fontSize: 16, fontWeight: 600, color: W95 }}>Recent activity</h3>
      {log.slice(0, 8).map((e, i) => (
        <div key={`${e.at}-${i}`} style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, minHeight: 30, borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 6 }}>
          <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: tone(e.outcome), overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.summary}</span>
          <span style={{ flex: 'none', fontSize: 12, color: W50 }}>{when(new Date(e.at))}</span>
        </div>
      ))}
    </section>
  );
}

export function DownloadsGuardSettings({ wide = true }: { wide?: boolean } = {}) {
  const [d, setD] = useState<DgView | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState('');
  const [signIn, setSignIn] = useState(false);
  const [bad, setBad] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<DgStatus | null>(null);
  const [log, setLog] = useState<DgLogEntry[]>([]);
  const [summary, setSummary] = useState<DgTrackerSummary | null>(null);
  const [preview, setPreview] = useState<DgPreview | null>(null);
  const [busy, setBusy] = useState<'preview' | 'run' | null>(null);
  const [confirming, setConfirming] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const started = useRef(false);

  const refresh = useCallback(() => {
    void loadDgStatus().then(r => { if (r.ok) { setStatus(r.data.status); setLog(Array.isArray(r.data.log) ? r.data.log : []); } });
  }, []);
  useEffect(() => {
    void loadDg().then(r => { if (r.ok) setD(r.data); else setFailed(true); });
    void loadDgTrackers().then(r => { if (r.ok) setSummary(r.data); });
    refresh();
  }, [refresh]);
  const running = !!status?.running;
  useEffect(() => {
    if (!d) return undefined;
    const t = window.setInterval(refresh, running || started.current ? 2000 : 30000);
    return () => window.clearInterval(t);
  }, [!!d, running, refresh]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!running) started.current = false; }, [running]);

  if (failed) return <div role="alert" style={{ ...panel, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W50 }}>{"Couldn't load"}</div>;
  if (!d) return <div aria-busy="true" style={{ ...panel, height: 120 }} />;
  const s = d.settings;

  const goSignIn = () => navigate('/login', { state: { from: location.pathname + location.search, signIn: true } });
  const put = (patch: Partial<DgSettings>) => {
    const before = d;
    setD({ ...d, settings: { ...s, ...patch } });
    setError('');
    setSignIn(false);
    setBad(b => { const n = { ...b }; Object.keys(patch).forEach(k => delete n[k]); return n; });
    void saveDg(patch).then(r => {
      if (r.ok) { setD(r.data); refresh(); return; }
      setD(before);
      if (r.signIn) { setSignIn(true); return; }
      if (r.errors) setBad(r.errors);
      setError(SAVE_ERROR);
    });
  };
  const doPreview = () => {
    setBusy('preview');
    setError('');
    void previewDg().then(r => {
      setBusy(null);
      if (r.ok) setPreview(r.data);
      else setError("Couldn't preview");
    });
  };
  const doRun = () => {
    setBusy('run');
    setError('');
    void runDg().then(r => {
      setBusy(null);
      setConfirming(false);
      if (r.ok) { started.current = true; refresh(); return; }
      if (r.signIn) { setSignIn(true); return; }
      setError(r.error || "Couldn't start");
    });
  };
  const ev = status?.evaluation;
  const sub = [ev?.available ? `${formatBytes(ev.free_bytes)} free` : null, `Floor ${gb(s.min_free_gb)}`].filter(Boolean).join(' · ');
  const off = !d.allowed;

  return (
    <div data-testid="downloads-guard" style={{ display: 'flex', flexDirection: 'column', gap: 16, fontFamily: FONT, color: W95, padding: wide ? 0 : '8px 16px 24px' }}>
      <style>{tvEditCss}{grCss}</style>
      <section className="gr-head" aria-label="Downloads guard" style={{ ...panel, display: 'flex', alignItems: 'center', gap: 16, padding: '12px 16px', minHeight: 64 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 12, rowGap: 2, flex: 1, minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>Downloads guard</h3>
          <span data-testid="dg-sub" style={{ fontSize: 12, color: W70 }}>{sub}</span>
        </div>
        <div className="gr-actions" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="button" className="tve-glass lp-focus" data-testid="dg-preview" disabled={busy !== null || running} onClick={doPreview}
            style={{ ...glassBtn, ...pill, opacity: busy !== null || running ? 0.5 : 1 }}>{busy === 'preview' ? 'Checking' : 'Preview'}</button>
          {confirming ? (
            <>
              <button type="button" className="tve-glass lp-focus" data-testid="dg-cancel" onClick={() => setConfirming(false)} style={{ ...glassBtn, ...pill }}>Cancel</button>
              <button type="button" className="lp-focus" data-testid="dg-confirm" disabled={busy !== null || running} onClick={doRun}
                style={{ ...glassBtn, ...pill, ...danger, opacity: busy !== null || running ? 0.5 : 1 }}>{busy === 'run' ? 'Deleting' : 'Delete permanently'}</button>
            </>
          ) : (
            <button type="button" className="lp-focus" data-testid="dg-run" disabled={busy !== null || running || off} onClick={() => setConfirming(true)}
              style={{ ...glassBtn, ...pill, ...danger, opacity: busy !== null || running || off ? 0.5 : 1 }}>{running ? 'Running' : 'Clean up now'}</button>
          )}
        </div>
      </section>
      {signIn && (
        <div role="alert" data-testid="dg-sign-in" style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 13, color: AMBER }}>
          <span style={{ flex: 1 }}>{SIGN_IN_TO_CHANGE}</span>
          <button type="button" className="tve-glass lp-focus" onClick={goSignIn} style={{ ...glassBtn, height: 32, padding: '0 14px', borderRadius: 16, fontSize: 13 }}>Sign in</button>
        </div>
      )}
      {error && <div role="alert" style={{ fontSize: 13, color: RED }}>{error}</div>}

      <div className="gr-grid">
        <section aria-label="Settings" style={{ ...panel, padding: 16, display: 'flex', flexDirection: 'column', gap: 18 }}>
          <Switch id="dg-enabled" label="Enabled" on={s.enabled} onChange={v => put({ enabled: v })} />
          <div>
            <h4 style={group}>Free space</h4>
            <div className="gr-f2">
              <Field id="dg-floor" label="Floor (GB)"><NumField id="dg-floor" value={s.min_free_gb} bad={!!bad.min_free_gb} onCommit={v => put({ min_free_gb: v })} /></Field>
              <Field id="dg-reclaim" label="Reclaim per run (GB)"><NumField id="dg-reclaim" value={s.reclaim_gb} bad={!!bad.reclaim_gb} onCommit={v => put({ reclaim_gb: v })} /></Field>
              <Field id="dg-interval" label="Check every (minutes)"><NumField id="dg-interval" value={s.check_interval_minutes} bad={!!bad.check_interval_minutes} onCommit={v => put({ check_interval_minutes: v })} /></Field>
              <Field id="dg-pill" label="Storage pill amount (GB)"><NumField id="dg-pill" value={s.pill_gb} bad={!!bad.pill_gb} onCommit={v => put({ pill_gb: v })} /></Field>
            </div>
          </div>
          <div>
            <h4 style={group}>Limits</h4>
            <div className="gr-f2">
              <Optional id="dg-daily" label="Daily limit (GB)" value={s.daily_cap_gb} fallback={d.defaults.daily_cap_gb} bad={!!bad.daily_cap_gb} onChange={v => put({ daily_cap_gb: v })} />
              <Field id="dg-max-run" label="Per-run limit (GB)"><NumField id="dg-max-run" value={s.max_run_gb} grouped bad={!!bad.max_run_gb} onCommit={v => put({ max_run_gb: v })} /></Field>
            </div>
          </div>
          <div>
            <h4 style={group}>What it may delete</h4>
            <div className="gr-f2">
              <Field id="dg-scope" label="Downloads"><Select id="dg-scope" value={s.scope} options={SCOPES} onChange={v => put({ scope: v })} /></Field>
              <Field id="dg-age" label="Keep downloads newer than"><Select id="dg-age" value={s.min_age_days} options={ageOptions(s.min_age_days)} onChange={v => put({ min_age_days: v })} /></Field>
              <Optional id="dg-seed" label="Minimum seed time (hours)" value={s.min_seed_hours} fallback={72} bad={!!bad.min_seed_hours} onChange={v => put({ min_seed_hours: v })} />
              <Optional id="dg-ratio" label="Minimum ratio" value={s.min_ratio} fallback={1} decimals={1} step={0.1} bad={!!bad.min_ratio} onChange={v => put({ min_ratio: v })} />
            </div>
          </div>
          <div>
            <h4 style={group}>Delete from these trackers first</h4>
            <TrackerList order={s.trackers} known={d.trackers} summary={summary} onChange={next => put({ trackers: next })} />
          </div>
          <div>
            <h4 style={group}>Never delete</h4>
            <NeverDelete tags={s.protected_tags ?? []} hashes={s.protected_hashes ?? []}
              onTags={next => put({ protected_tags: next })} onHashes={next => put({ protected_hashes: next })} />
          </div>
          <div>
            <h4 style={group}>Folder</h4>
            <FolderField value={s.folder} placeholder={d.media_folder} bad={!!bad.folder} onCommit={v => put({ folder: v })} />
          </div>
        </section>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          <StatusPanel status={status} allowed={d.allowed} />
          {preview?.error && <div role="alert" data-testid="dg-preview-error" style={{ fontSize: 13, color: AMBER }}>{preview.error}</div>}
          {preview && !preview.error && (
            preview.entries.length
              ? <Entries title={`Would delete ${formatBytes(preview.planned_bytes)} of ${formatBytes(preview.target_bytes)}`} entries={preview.entries} testid="dg-preview-list" />
              : <div data-testid="dg-preview-none" style={{ ...panel, padding: 16, fontSize: 13, color: W70 }}>Nothing to delete</div>
          )}
          <Entries title={status?.last_result?.dry_run ? 'Last preview' : 'Deleted last time'} entries={status?.last_result?.deleted ?? []} testid="dg-last-list" />
          <Activity log={log} />
        </div>
      </div>
    </div>
  );
}
