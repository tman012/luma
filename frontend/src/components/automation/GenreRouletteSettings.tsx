/** v252 Settings > Automation > Genre roulette (mockup GR5; labels in Luma's sentence case, v174): each night it spins genres and grabs well-known films the
 * library doesn't have, natively (TMDb discover, tracker search, qBittorrent, the movie importer). Owner only; changes,
 * runs and previews need the signed-in owner. Settings save as they change. */
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { FONT } from '@/components/start/StartUI';
import { apiUrl } from '@/urlHelpers';
import { Modal, Select, Toggle, SAVE_ERROR, field, glassBtn, primaryBtn, tvEditCss } from '@/components/library/TvEdit';
import {
  loadGr, loadGrLive, previewGr, runGr, saveGr,
  type GrPreview, type GrRun, type GrSettings, type GrStatus, type GrView,
} from './genreRouletteApi';

const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const RED = '#fca5a5';
const ET = 'America/New_York';
const panel: CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', boxSizing: 'border-box' };
const group: CSSProperties = { margin: '0 0 8px', fontSize: 11, fontWeight: 700, letterSpacing: '0.02em', color: '#a5b4fc' };
const lab: CSSProperties = { display: 'block', marginBottom: 6, fontSize: 12, fontWeight: 500, color: W70 };
const tile: CSSProperties = { borderRadius: 12, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.06)', padding: '10px 12px', minWidth: 0 };
const pill: CSSProperties = { borderRadius: 20, height: 40, padding: '0 20px' };

export const DISPLAY: Record<string, string> = { 'Science Fiction': 'Sci-Fi' };
export const LANGUAGES: Record<string, string> = {
  any: 'Any', en: 'English', fr: 'French', es: 'Spanish', de: 'German', it: 'Italian', ja: 'Japanese', ko: 'Korean',
  zh: 'Chinese', hi: 'Hindi', sv: 'Swedish', da: 'Danish', no: 'Norwegian', pt: 'Portuguese', ru: 'Russian',
};
const DAYS = [{ id: 'daily' as const, label: 'Every day' }, { id: 'weekdays' as const, label: 'Weekdays' }, { id: 'weekends' as const, label: 'Weekends' }];

export const grCss = `
.gr-grid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(280px,1fr);gap:16px;align-items:start}
.gr-f4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.gr-f3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.gr-f2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.gr-chip:hover{border-color:rgba(255,255,255,.3)!important}
.gr-hist:hover{background:rgba(255,255,255,.04)}
.gr-step:hover{color:#fff!important}
.gr-grid.gr-hgrid{align-items:stretch}
.gr-hpanel{display:flex;flex-direction:column;min-height:0}
.gr-hbox{flex:1 1 auto;min-height:176px;position:relative}
.gr-hscroll{position:absolute;inset:0;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.16) transparent}
.gr-hscroll::-webkit-scrollbar{display:block;width:6px}
.gr-hscroll::-webkit-scrollbar-track{background:transparent}
.gr-hscroll::-webkit-scrollbar-thumb{background:rgba(255,255,255,.16);border-radius:3px}
.gr-hscroll::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.28)}
@media (max-width:899px){.gr-grid{grid-template-columns:minmax(0,1fr)}.gr-hbox{position:static;min-height:0}.gr-hscroll{position:static;max-height:360px}}
@media (max-width:599px){.gr-f4,.gr-f3{grid-template-columns:repeat(2,minmax(0,1fr))}.gr-head{flex-wrap:wrap}.gr-actions{width:100%;justify-content:flex-end}}
`;

// ---------------------------------------------------------------- time helpers (ET)

export function clock(hhmm: string): string {
  const [h = 0, m = 0] = hhmm.split(':').map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}
const TIMES = Array.from({ length: 144 }, (_, i) => {
  const id = `${String(Math.floor(i / 6)).padStart(2, '0')}:${String((i % 6) * 10).padStart(2, '0')}`;
  return { id, label: clock(id) };
});
const etDay = (d: Date) => new Intl.DateTimeFormat('en-CA', { timeZone: ET, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
const etTime = (d: Date) => new Intl.DateTimeFormat('en-US', { timeZone: ET, hour: 'numeric', minute: '2-digit' }).format(d).replace(/\s/g, ' ');
const etWeekday = (d: Date) => new Intl.DateTimeFormat('en-US', { timeZone: ET, weekday: 'short' }).format(d);
/** "Today 3:00 AM", "Tomorrow 10:50 PM", else "Thu 3:00 AM". */
export function when(d: Date, now = new Date()): string {
  const day = etDay(d);
  const rel = day === etDay(now) ? 'Today' : day === etDay(new Date(now.getTime() + 86400000)) ? 'Tomorrow'
    : day === etDay(new Date(now.getTime() - 86400000)) ? 'Yesterday' : etWeekday(d);
  return `${rel} ${etTime(d)}`;
}
/** "Wed Oct 21, 3:00 AM" */
export const stamp = (d: Date) => `${etWeekday(d)} ${new Intl.DateTimeFormat('en-US', { timeZone: ET, month: 'short', day: 'numeric' }).format(d)}, ${etTime(d)}`;
export const result = (r: { dry?: boolean; grabbed?: number; wanted?: number; error?: string | null; status?: string }) =>
  r.status === 'running' ? 'Running' : r.error ? r.error : `${r.dry ? 'Would add' : 'Added'} ${r.grabbed ?? 0} of ${r.wanted ?? 0}`;
export const dot = (r: Pick<GrRun, 'grabbed' | 'wanted' | 'status' | 'error'>) =>
  r.status === 'running' ? '#60a5fa' : r.error || !r.grabbed ? '#f87171' : r.grabbed < r.wanted ? '#fbbf24' : '#4ade80';
const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

// ---------------------------------------------------------------- controls

export function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return <div style={{ minWidth: 0 }}><label htmlFor={id} id={`${id}-label`} style={lab}>{label}</label>{children}</div>;
}

/** A number field that saves on blur or Enter; a bad value goes back to the saved one. */
export function NumField({ id, value, onCommit, step = 1, bad, disabled, decimals = 0, grouped = false, style }: {
  id: string; value: number; onCommit: (n: number) => void; step?: number; bad?: boolean; disabled?: boolean; decimals?: number; grouped?: boolean; style?: CSSProperties;
}) {
  // A missing value (undefined, null, NaN) shows an empty field, never the text 'undefined'.
  const show = (n: number) => (typeof n !== 'number' || !Number.isFinite(n) ? '' : grouped ? n.toLocaleString('en-US') : decimals ? n.toFixed(decimals) : String(n));
  const [draft, setDraft] = useState(show(value));
  useEffect(() => setDraft(show(value)), [value]); // eslint-disable-line react-hooks/exhaustive-deps
  const commit = () => {
    const n = Number(draft.replace(/,/g, '').trim());
    if (draft.trim() === '' || !Number.isFinite(n)) { setDraft(show(value)); return; }
    const v = decimals ? Math.round(n / step) * step : Math.round(n);
    if (v !== value) onCommit(Number(v.toFixed(decimals))); else setDraft(show(value));
  };
  return (
    <input id={id} className="tve-in" inputMode={decimals ? 'decimal' : 'numeric'} value={draft} disabled={disabled} aria-invalid={bad || undefined}
      data-testid={`${id}-input`} onChange={e => setDraft(e.target.value)} onBlur={commit}
      onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
      style={{ ...field, ...(bad ? { borderColor: '#ef4444' } : null), ...(disabled ? { opacity: 0.4 } : null), ...style }} />
  );
}

export function Stepper({ id, value, min, max, onChange }: { id: string; value: number; min: number; max: number; onChange: (n: number) => void }) {
  const b = (label: string, d: number, text: string) => (
    <button type="button" className="gr-step lp-focus" aria-label={label} disabled={value + d < min || value + d > max}
      onClick={() => onChange(value + d)} data-testid={`${id}-${d < 0 ? 'minus' : 'plus'}`}
      style={{ width: 22, height: 28, border: 'none', background: 'none', color: W70, fontSize: 16, cursor: 'pointer', padding: 0, opacity: value + d < min || value + d > max ? 0.3 : 1 }}>{text}</button>
  );
  return (
    <div style={{ ...field, display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingRight: 6 }}>
      <output id={id} aria-labelledby={`${id}-label`} data-testid={`${id}-value`}>{value}</output>
      <span style={{ display: 'flex' }}>{b('Fewer', -1, '−')}{b('More', 1, '+')}</span>
    </div>
  );
}

export type ChipState = 'on' | 'off' | 'out';
export function Chip({ name, state, onClick }: { name: string; state: ChipState; onClick: () => void }) {
  const label = DISPLAY[name] || name;
  const s: CSSProperties = state === 'on'
    ? { background: 'rgba(110,134,180,0.38)', borderColor: 'rgba(140,165,215,0.45)', color: W95 }
    : state === 'out'
      ? { background: 'rgba(255,255,255,0.03)', borderColor: 'rgba(255,255,255,0.14)', color: W50, textDecoration: 'line-through' }
      : { background: 'none', borderColor: 'rgba(255,255,255,0.14)', color: W95 };
  return (
    <button type="button" className="gr-chip lp-focus" data-testid="gr-chip" data-state={state} aria-pressed={state === 'on' ? true : state === 'out' ? 'mixed' : false}
      aria-label={state === 'on' ? `${label}, included` : state === 'out' ? `${label}, excluded` : label} onClick={onClick}
      style={{ height: 32, padding: '0 10px', borderRadius: 6, border: '1px solid', fontSize: 13, fontFamily: FONT, cursor: 'pointer', ...s }}>
      {state === 'on' ? '✓ ' : state === 'out' ? <span style={{ textDecoration: 'none', display: 'inline-block', marginRight: 4 }}>—</span> : null}{label}
    </button>
  );
}

export function Poster({ path, title }: { path: string | null; title: string }) {
  const box: CSSProperties = { width: 20, height: 30, borderRadius: 3, flex: 'none', background: 'rgba(255,255,255,0.08)', objectFit: 'cover' };
  return path ? <img src={apiUrl(`/api/img/tmdb?path=${encodeURIComponent(path)}&size=w92`)} alt="" aria-label={title} style={box} loading="lazy" /> : <span style={box} />;
}

function Picks({ run }: { run: Partial<GrRun> }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '4px 0 10px 18px' }}>
      {(run.picks || []).map(p => (
        <div key={p.tmdb_id} data-testid="gr-pick" style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, minWidth: 0 }}>
          <Poster path={p.poster_path} title={p.title} />
          <span style={{ color: W95, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.title}</span>
          <span style={{ color: W50, fontSize: 12, flex: 'none' }}>{[p.year, DISPLAY[p.genre] || p.genre].filter(Boolean).join(' · ')}</span>
        </div>
      ))}
      {(run.failures || []).map(f => (
        <div key={f.genre} data-testid="gr-failure" style={{ fontSize: 12, color: RED }}>⚠ {DISPLAY[f.genre] || f.genre}: {lower(f.reason)}</div>
      ))}
      {run.error && <div style={{ fontSize: 12, color: RED }}>⚠ {run.error}</div>}
    </div>
  );
}

/** Skip reason labels (AmEng) — short refuse lines only. */
const SKIP_REASON: Record<string, string> = {
  same_collection: 'Same collection',
  owned_collection: 'Same collection',
  owned: 'Already in the library',
  no_release: 'No release found',
  refused: 'No release found',
};

/** Grabbed (or dry would-grab) titles belong in the default list. */
export function isGrabPick(run: Pick<GrRun, 'dry'>, pick: { grabbed?: boolean }): boolean {
  if (run.dry) return true;
  return pick.grabbed !== false;
}

export type HistDetail = {
  skips: { key: string; title: string; meta: string; reason: string }[];
  failures: { genre: string; reason: string }[];
  error: string | null;
  summary: string | null;
};

/** Default (collapsed) history rows: grabbed titles + compact zero-grab rows. Attempts behind disclosure. */
export type HistRow =
  | { kind: 'grab'; key: string; runId: number; at: number; dry: boolean; pick: NonNullable<GrRun['picks']>[number]; disclose: boolean }
  | { kind: 'compact'; key: string; runId: number; at: number; dry: boolean; label: 'Running' | 'Nothing added'; error: string | null; disclose: boolean };

export function runDetails(r: GrRun): HistDetail | null {
  const skips: HistDetail['skips'] = [];
  const bag = r.skips && typeof r.skips === 'object' && !Array.isArray(r.skips) ? r.skips : {};
  for (const [why, list] of Object.entries(bag)) {
    if (!Array.isArray(list)) continue;
    const reason = SKIP_REASON[why] || why.replace(/_/g, ' ');
    list.forEach((s, i) => {
      const title = String(s?.title || 'Untitled');
      const meta = [s?.year, DISPLAY[String(s?.genre || '')] || s?.genre].filter(Boolean).join(' · ');
      const detail = s?.detail ? String(s.detail) : '';
      skips.push({ key: `${r.id}-s-${why}-${s?.tmdb_id ?? i}`, title, meta, reason: detail || reason });
    });
  }
  const failures = (r.failures || []).map(f => ({ genre: f.genre, reason: f.reason }));
  const error = r.error || null;
  // PM fold-in: chevron only when there is something to expand (skips / failures / error).
  // Summary alone (Added N of M) never opens a disclosure.
  if (!skips.length && !failures.length && !error) return null;
  const summary = (r.grabbed || r.wanted) ? result(r) : null;
  return { skips, failures, error, summary };
}

export function flattenHistory(history: GrRun[]): HistRow[] {
  const rows: HistRow[] = [];
  for (const r of history) {
    const at = r.started_at;
    const dry = !!r.dry;
    const grabs = (r.picks || []).filter(p => isGrabPick(r, p));
    const details = runDetails(r);
    const disclose = !!details && (details.skips.length > 0 || details.failures.length > 0 || !!details.error);
    if (grabs.length) {
      grabs.forEach((p, i) => {
        rows.push({
          kind: 'grab', key: `${r.id}-p-${p.tmdb_id}-${i}`, runId: r.id, at, dry, pick: p,
          disclose: disclose && i === grabs.length - 1,
        });
      });
      continue;
    }
    if (r.status === 'running') {
      rows.push({ kind: 'compact', key: `${r.id}-x`, runId: r.id, at, dry, label: 'Running', error: null, disclose: false });
      continue;
    }
    // Zero-grab night: compact row (Nothing added / error), disclosure when attempts exist.
    const hasAttempt = !!(details && (details.skips.length || details.failures.length || details.error));
    rows.push({
      kind: 'compact', key: `${r.id}-x`, runId: r.id, at, dry,
      label: 'Nothing added', error: r.error || null,
      disclose: hasAttempt,
    });
  }
  return rows;
}

function HistChevron({ open }: { open: boolean }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      style={{ transition: 'transform 150ms', transform: open ? 'rotate(180deg)' : 'none', color: 'rgba(255,255,255,0.3)' }}
      aria-hidden>
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function HistDetails({ run }: { run: GrRun }) {
  const d = runDetails(run);
  if (!d) return null;
  return (
    <div data-testid="gr-history-details" style={{ borderTop: '1px solid rgba(255,255,255,0.04)', padding: '8px 4px 12px 34px', display: 'flex', flexDirection: 'column', gap: 6 }}>
      {d.summary && <div data-testid="gr-history-summary" style={{ fontSize: 12, color: W50 }}>{d.summary}</div>}
      {d.skips.map(s => (
        <div key={s.key} data-testid="gr-history-skip" style={{ fontSize: 12, color: W70, display: 'flex', gap: 8, minWidth: 0 }}>
          <span style={{ color: W95, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</span>
          {s.meta ? <span style={{ color: W50, flex: 'none' }}>{s.meta}</span> : null}
          <span style={{ color: RED, flex: 'none' }}>{s.reason}</span>
        </div>
      ))}
      {d.failures.map(f => (
        <div key={f.genre} data-testid="gr-failure" style={{ fontSize: 12, color: RED }}>⚠ {DISPLAY[f.genre] || f.genre}: {lower(f.reason)}</div>
      ))}
      {d.error && <div data-testid="gr-history-error" style={{ fontSize: 12, color: RED }}>⚠ {d.error}</div>}
    </div>
  );
}

function StatusPanel({ status, history }: { status: GrStatus; history: GrRun[] }) {
  const last = status.last_run;
  const state = status.running ? { text: 'Running', color: '#60a5fa' } : status.enabled ? { text: 'Idle', color: '#4ade80' } : { text: 'Off', color: W50 };
  const tiles: [string, string][] = [
    ['Last run', last ? when(new Date(last.started_at * 1000)) : '—'],
    ['Next run', status.next_run ? when(new Date(status.next_run)) : '—'],
    ['Last result', last ? result(last) : '—'],
    ['Failures (7 days)', String(status.failures_7d)],
  ];
  const rows = flattenHistory(history);
  const byId = new Map(history.map(r => [r.id, r]));
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const toggle = (id: number) => setOpen(cur => ({ ...cur, [id]: !cur[id] }));
  return (
    <section aria-label="Status" data-testid="gr-status" className="gr-hpanel" style={{ ...panel, padding: '16px 16px 8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: W95 }}>Status</h3>
        <span data-testid="gr-state" style={{ fontSize: 12, color: state.color }}>● {state.text}</span>
      </div>
      <div className="gr-f2" style={{ gap: 8, marginBottom: 18 }}>
        {tiles.map(([k, v]) => (
          <div key={k} style={tile} data-testid="gr-tile">
            <div style={{ fontSize: 12, color: W70, marginBottom: 4 }}>{k}</div>
            <div style={{ fontSize: 15, color: W95, overflowWrap: 'anywhere' }}>{v}</div>
          </div>
        ))}
      </div>
      <h4 style={group}>History</h4>
      <div className="gr-hbox"><div role="list" data-testid="gr-history" className="gr-hscroll">
        {rows.length === 0 && <div style={{ padding: '12px 0', fontSize: 13, color: W50 }}>No runs yet</div>}
        {rows.map(row => {
          const isOpen = !!open[row.runId];
          const run = byId.get(row.runId);
          return (
            <div key={row.key} role="listitem" data-testid="gr-history-row" data-kind={row.kind} data-run={row.runId}
              style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
              <div style={{ minHeight: 44, display: 'flex', alignItems: 'center', gap: 10, padding: '8px 4px', fontSize: 13, minWidth: 0 }}>
                {row.kind === 'grab' && (
                  <>
                    <Poster path={row.pick.poster_path} title={row.pick.title} />
                    <span data-testid="gr-pick" style={{ color: W95, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0, fontWeight: 600 }}>{row.pick.title}</span>
                    <span style={{ color: W50, fontSize: 12, flex: 'none' }}>{[row.pick.year, DISPLAY[row.pick.genre] || row.pick.genre].filter(Boolean).join(' · ')}</span>
                  </>
                )}
                {row.kind === 'compact' && (
                  row.error
                    ? <span data-testid="gr-history-error" style={{ flex: 1, fontSize: 12, color: RED }}>⚠ {row.error}</span>
                    : <span data-testid="gr-history-empty" style={{ flex: 1, color: W50, fontWeight: 500 }}>{row.label}</span>
                )}
                <span style={{ flex: 'none', fontSize: 12, color: W50, textAlign: 'right' }}>
                  {stamp(new Date(row.at * 1000))}
                  {row.dry && <span style={{ display: 'block' }}>Dry run</span>}
                </span>
                {row.disclose && (
                  <button type="button" className="lp-focus" data-testid="gr-history-disclose" aria-expanded={isOpen}
                    aria-label={isOpen ? 'Hide details' : 'Show details'} onClick={() => toggle(row.runId)}
                    style={{ flex: 'none', width: 44, height: 44, margin: '-4px', border: 'none', background: 'none', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0 }}>
                    <HistChevron open={isOpen} />
                  </button>
                )}
              </div>
              {row.disclose && isOpen && run && <HistDetails run={run} />}
            </div>
          );
        })}
      </div></div>
    </section>
  );
}

// ---------------------------------------------------------------- page

const arr = <T,>(x: T[] | null | undefined): T[] => (Array.isArray(x) ? x : []);
const isObj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x);
/** A view missing its settings or status is a failed load, never a crash; list fields that aren't arrays read as empty. */
export function normGr(v: unknown): GrView | null {
  if (!isObj(v) || !isObj(v.settings) || !isObj(v.status)) return null;
  const s = v.settings as unknown as GrSettings;
  return { ...(v as unknown as GrView), genres: arr((v as unknown as GrView).genres), profiles: arr((v as unknown as GrView).profiles),
    history: normHistory((v as unknown as GrView).history),
    settings: { ...s, include_genres: arr(s.include_genres), exclude_genres: arr(s.exclude_genres) } };
}
const normHistory = (h: GrRun[] | null | undefined): GrRun[] =>
  arr(h).filter(isObj).map(r => ({
    ...r,
    picks: arr(r.picks),
    failures: arr(r.failures),
    skips: isObj(r.skips) ? r.skips as GrRun['skips'] : {},
  }));

export function GenreRouletteSettings({ wide = true }: { wide?: boolean } = {}) {
  const [d, setD] = useState<GrView | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState('');
  const [bad, setBad] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<GrPreview | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const busy = useRef(false);

  useEffect(() => {
    void loadGr().then(r => { const v = r.ok ? normGr(r.data) : null; if (v) setD(v); else setFailed(true); });
  }, []);

  const live = useCallback(() => {
    void loadGrLive().then(r => {
      if (!r.ok || !isObj(r.data) || !isObj(r.data.status)) return;
      const { status, history, preview: pv } = r.data;
      setD(cur => (cur ? { ...cur, status, history: normHistory(history) } : cur));
      setPreview(isObj(pv) ? pv : null);
    });
  }, []);
  const fast = !!d?.status.running || !!preview?.running;
  useEffect(() => {
    if (!d) return undefined;
    const t = window.setInterval(live, fast ? 3000 : 30000);
    return () => window.clearInterval(t);
  }, [!!d, fast, live]); // eslint-disable-line react-hooks/exhaustive-deps

  if (failed) return <div role="alert" style={{ ...panel, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W50 }}>{"Couldn't load"}</div>;
  if (!d) return <div aria-busy="true" style={{ ...panel, height: 120 }} />;
  const s = d.settings;

  const put = (patch: Partial<GrSettings>) => {
    const before = d;
    setD({ ...d, settings: { ...s, ...patch } });
    setError('');
    setBad(b => { const n = { ...b }; Object.keys(patch).forEach(k => delete n[k]); return n; });
    void saveGr(patch).then(r => {
      const v = r.ok ? normGr(r.data) : null;
      if (v) { setD(v); return; }
      setD(before);
      if (!r.ok && r.errors) setBad(r.errors);
      setError(SAVE_ERROR);
    });
  };
  const start = (kind: 'run' | 'preview') => {
    if (busy.current) return;
    busy.current = true;
    setError('');
    if (kind === 'preview') { setPreview({ running: true, result: null }); setShowPreview(true); }
    void (kind === 'run' ? runGr() : previewGr()).then(r => {
      busy.current = false;
      if (!r.ok && r.status !== 409) { setError("Couldn't start"); if (kind === 'preview') setShowPreview(false); }
      live();
    });
  };

  const stateOf = (g: string): ChipState => (s.include_genres.includes(g) ? 'on' : s.exclude_genres.includes(g) ? 'out' : 'off');
  const cycle = (g: string) => {
    const st = stateOf(g);
    const inc = s.include_genres.filter(x => x !== g);
    const exc = s.exclude_genres.filter(x => x !== g);
    if (st === 'off') inc.push(g);
    if (st === 'on') exc.push(g);
    put({ include_genres: inc, exclude_genres: exc });
  };
  const profiles = d.profiles.map(p => ({ id: p.id, label: p.name }));
  const profileId = s.profile_id ?? d.profiles.find(p => p.default)?.id ?? d.profiles[0]?.id ?? null;
  const last = d.status.last_run;
  const sub = [d.status.next_run && s.enabled ? `Next run ${when(new Date(d.status.next_run))}` : null,
    last ? `Last: ${last.error ? last.error : `${last.grabbed} of ${last.wanted}`}` : null].filter(Boolean).join(' · ');
  const running = d.status.running;

  return (
    <div data-testid="genre-roulette" style={{ display: 'flex', flexDirection: 'column', gap: 16, fontFamily: FONT, color: W95, padding: wide ? 0 : '8px 16px 24px' }}>
      <style>{tvEditCss}{grCss}</style>
      <section className="gr-head" aria-label="Genre roulette" style={{ ...panel, display: 'flex', alignItems: 'center', gap: 16, padding: '12px 16px', minHeight: 64 }}>
        <Toggle id="gr-enabled" on={s.enabled} onChange={v => put({ enabled: v })} label="Genre roulette" />
        <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 12, rowGap: 2, flex: 1, minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>Genre roulette</h3>
          {sub && <span data-testid="gr-sub" style={{ fontSize: 12, color: W70 }}>{sub}</span>}
        </div>
        <div className="gr-actions" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span id="gr-dry-label" style={{ fontSize: 13, color: W70 }}>Dry run</span>
          <Toggle id="gr-dry" on={s.dry_run} onChange={v => put({ dry_run: v })} />
          <button type="button" className="tve-glass lp-focus" data-testid="gr-preview" disabled={!!preview?.running} onClick={() => start('preview')}
            style={{ ...glassBtn, ...pill, opacity: preview?.running ? 0.5 : 1 }}>Preview</button>
          <button type="button" className="tve-primary lp-focus" data-testid="gr-run" disabled={running} onClick={() => start('run')}
            style={{ ...primaryBtn(!running), ...pill }}>{running ? 'Running' : 'Run now'}</button>
        </div>
      </section>
      {error && <div role="alert" style={{ fontSize: 13, color: RED }}>{error}</div>}

      <div className="gr-grid gr-hgrid">
        <section aria-label="Settings" style={{ ...panel, padding: 16, display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div>
            <h4 style={group}>Schedule &amp; picks</h4>
            <div className="gr-f4">
              <Field id="gr-time" label="Run time"><Select id="gr-time" value={s.time} options={TIMES.some(t => t.id === s.time) ? TIMES : [{ id: s.time, label: clock(s.time) }, ...TIMES]} onChange={v => put({ time: v })} /></Field>
              <Field id="gr-days" label="Days"><Select id="gr-days" value={s.days} options={DAYS} onChange={v => put({ days: v })} /></Field>
              <Field id="gr-genres-per-run" label="Genres per run"><Stepper id="gr-genres-per-run" value={s.genres_per_run} min={1} max={d.genres.length} onChange={v => put({ genres_per_run: v })} /></Field>
              <Field id="gr-movies-per-genre" label="Movies per genre"><Stepper id="gr-movies-per-genre" value={s.movies_per_genre} min={1} max={5} onChange={v => put({ movies_per_genre: v })} /></Field>
            </div>
          </div>
          <div>
            <h4 style={group}>Genres</h4>
            <div role="group" aria-label="Genres" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {d.genres.map(g => <Chip key={g} name={g} state={stateOf(g)} onClick={() => cycle(g)} />)}
            </div>
          </div>
          <div>
            <h4 style={group}>Filters</h4>
            <div className="gr-f2">
              <div style={{ minWidth: 0 }}>
                <span id="gr-years-label" style={lab}>Years</span>
                <div role="group" aria-labelledby="gr-years-label" style={{ ...field, display: 'flex', alignItems: 'center', gap: 4, padding: '0 4px', ...(bad.year_to ? { borderColor: '#ef4444' } : null) }}>
                  <NumField id="gr-year-from" value={s.year_from} onCommit={v => put({ year_from: v })} style={{ border: 'none', background: 'none', width: 64, padding: '0 8px' }} />
                  <span aria-hidden="true" style={{ color: W50 }}>–</span>
                  <NumField id="gr-year-to" value={s.year_to} onCommit={v => put({ year_to: v })} style={{ border: 'none', background: 'none', width: 64, padding: '0 8px' }} />
                </div>
              </div>
              <Field id="gr-language" label="Language"><Select id="gr-language" value={s.language} options={Object.entries(LANGUAGES).map(([id, label]) => ({ id, label }))} onChange={v => put({ language: v })} /></Field>
            </div>
          </div>
          <div>
            <h4 style={group}>Ratings</h4>
            <div className="gr-f3">
              <Field id="gr-min-votes" label="Minimum TMDb votes"><NumField id="gr-min-votes" value={s.min_votes} grouped bad={!!bad.min_votes} onCommit={v => put({ min_votes: v })} /></Field>
              <Field id="gr-imdb-rating" label="Minimum IMDb rating"><NumField id="gr-imdb-rating" value={s.imdb_min_rating} decimals={1} step={0.1} disabled={!d.imdb_active} bad={!!bad.imdb_min_rating} onCommit={v => put({ imdb_min_rating: v })} /></Field>
              <Field id="gr-imdb-votes" label="Minimum IMDb votes"><NumField id="gr-imdb-votes" value={s.imdb_min_votes} grouped disabled={!d.imdb_active} bad={!!bad.imdb_min_votes} onCommit={v => put({ imdb_min_votes: v })} /></Field>
            </div>
          </div>
          <div>
            <h4 style={group}>Quality</h4>
            <div className="gr-f3" style={{ alignItems: 'end' }}>
              <Field id="gr-profile" label="Quality profile"><Select id="gr-profile" value={profileId} options={profiles} onChange={v => put({ profile_id: v })} /></Field>
              <Field id="gr-seeders" label="Minimum seeders"><NumField id="gr-seeders" value={s.min_seeders} bad={!!bad.min_seeders} onCommit={v => put({ min_seeders: v })} /></Field>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, height: 40 }}>
                <span id="gr-collection-label" style={{ fontSize: 13, color: W95 }}>Skip same collection</span>
                <Toggle id="gr-collection" on={s.skip_same_collection} onChange={v => put({ skip_same_collection: v })} />
              </div>
            </div>
          </div>
        </section>
        <StatusPanel status={d.status} history={d.history} />
      </div>

      {showPreview && (
        <Modal title="Preview" testid="gr-preview-modal" onClose={() => setShowPreview(false)}
          footer={<button type="button" className="tve-glass lp-focus" style={glassBtn} onClick={() => setShowPreview(false)}>Close</button>}>
          {!preview || preview.running || !preview.result
            ? <div aria-busy="true" style={{ fontSize: 14, color: W70 }}>Spinning…</div>
            : <div data-testid="gr-preview-result">
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>{result({ ...preview.result, dry: true })}</div>
                <Picks run={preview.result} />
              </div>}
        </Modal>
      )}
    </div>
  );
}
