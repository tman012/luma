/** v253 Settings > Automation > Anticipated (prod's anticipated watchlist, native): every few hours the top anticipated
 * shows (TV Add, tv.db) and movies (the movie watchlist) on Trakt are added. Same layout as Genre roulette. Owner only;
 * changes and Run now need the signed-in owner. Settings save as they change. */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Select, Toggle, SAVE_ERROR, primaryBtn, tvEditCss } from '@/components/library/TvEdit';
import { ADD_MONITOR, type MonitorOption } from '@/components/library/tvCalendarApi';
import { Field, LANGUAGES, NumField, Stepper, stamp, when } from './GenreRouletteSettings';
import { loadAn, loadAnLive, runAn, saveAn, type AnCounts, type AnEntry, type AnGroup, type AnPatch, type AnStatus, type AnView, type Kind } from './anticipatedApi';

const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const RED = '#fca5a5';
const panel: CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', boxSizing: 'border-box' };
const group: CSSProperties = { margin: '0 0 8px', fontSize: 11, fontWeight: 700, letterSpacing: '0.02em', color: '#a5b4fc' };
const tile: CSSProperties = { borderRadius: 12, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.06)', padding: '10px 12px', minWidth: 0 };
const pill: CSSProperties = { borderRadius: 20, height: 40, padding: '0 20px' };
const rule: CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, minHeight: 40 };

const css = '@media (max-width:599px){.an-rules{grid-template-columns:minmax(0,1fr)!important}}';
const HOURS = Array.from({ length: 24 }, (_, i) => ({ id: i + 1, label: i ? `${i + 1} hours` : '1 hour' }));
const BUSY = [{ id: 'skip' as const, label: 'Skip this run' }, { id: 'retry' as const, label: 'Retry in 15 min' }];
const KEPT = [50, 200, 500].map(n => ({ id: n, label: String(n) }));
const NAMES: Record<Kind, string> = { shows: 'Shows', movies: 'Movies' };
export const SKIPPED: Record<string, string> = {
  existing: 'already added', plex: 'in Plex', language: 'language', no_tvdb: 'no TVDB ID', no_tmdb: 'no TMDB ID',
  removed: 'removed before', grabbed: 'already grabbed',
};

/** "Added 3 of 20" or the group's error. */
export const counts = (c: AnCounts | null) => (!c ? '—' : c.error ? c.error : `Added ${c.added} of ${c.checked}`);
/** "in Plex 4, language 2" */
export const skips = (c: AnCounts | null) =>
  (!c ? '' : Object.entries(c.skipped && typeof c.skipped === 'object' ? c.skipped : {}).filter(([, n]) => n > 0).map(([k, n]) => `${SKIPPED[k] || k} ${n}`).join(', '));

function Lang({ code, on, onClick }: { code: string; on: boolean; onClick: () => void }) {
  const s: CSSProperties = on
    ? { background: 'rgba(110,134,180,0.38)', borderColor: 'rgba(140,165,215,0.45)', color: W95 }
    : { background: 'none', borderColor: 'rgba(255,255,255,0.14)', color: W95 };
  return (
    <button type="button" className="gr-chip lp-focus" data-testid="an-lang" data-code={code} aria-pressed={on} onClick={onClick}
      style={{ height: 32, padding: '0 10px', borderRadius: 6, border: '1px solid', fontSize: 13, fontFamily: FONT, cursor: 'pointer', ...s }}>
      {on ? '✓ ' : null}{LANGUAGES[code] || code}
    </button>
  );
}

function Rule({ id, label, on, onChange }: { id: string; label: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div style={rule}>
      <span id={`${id}-label`} style={{ fontSize: 13, color: W95 }}>{label}</span>
      <Toggle id={id} on={on} onChange={onChange} />
    </div>
  );
}

function GroupSettings({ kind, g, d, put }: { kind: Kind; g: AnGroup; d: AnView; put: (p: Partial<AnGroup>) => void }) {
  const p = `an-${kind}`;
  const profs = kind === 'shows' ? d.profiles : d.movie_profiles;
  const profileId = g.profile_id ?? profs.find(x => x.default)?.id ?? profs[0]?.id ?? null;
  const pathId = g.path_id ?? d.libraries[0]?.id ?? null;
  const flip = (code: string) => put({ languages: g.languages.includes(code) ? g.languages.filter(x => x !== code) : [...g.languages, code] });
  return (
    <section aria-label={NAMES[kind]} data-testid={p} style={{ ...panel, padding: 16, display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Toggle id={`${p}-enabled`} on={g.enabled} onChange={v => put({ enabled: v })} label={NAMES[kind]} />
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{NAMES[kind]}</h3>
      </div>
      <div className="gr-f3">
        <Field id={`${p}-limit`} label={`${NAMES[kind]} per run`}><Stepper id={`${p}-limit`} value={g.limit} min={1} max={40} onChange={v => put({ limit: v })} /></Field>
        <Field id={`${p}-profile`} label="Quality profile"><Select id={`${p}-profile`} value={profileId} options={profs.map(x => ({ id: x.id, label: x.name }))} onChange={v => put({ profile_id: v })} /></Field>
        <Field id={`${p}-page`} label="List page"><NumField id={`${p}-page`} value={g.page} onCommit={v => put({ page: v })} /></Field>
        {kind === 'shows' && <>
          <Field id={`${p}-library`} label="Library"><Select id={`${p}-library`} value={pathId} options={d.libraries.map(x => ({ id: x.id, label: x.name }))} onChange={v => put({ path_id: v })} /></Field>
          <Field id={`${p}-monitor`} label="Monitor"><Select id={`${p}-monitor`} value={g.monitor ?? 'all'} options={ADD_MONITOR} onChange={v => put({ monitor: v as MonitorOption })} /></Field>
        </>}
      </div>
      <div>
        <h4 style={group}>Original languages</h4>
        <div role="group" aria-label="Original languages" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {d.languages.map(c => <Lang key={c} code={c} on={g.languages.includes(c)} onClick={() => flip(c)} />)}
        </div>
      </div>
      <div className="gr-f2 an-rules" style={{ columnGap: 24, rowGap: 0 }}>
        <Rule id={`${p}-search`} label="Search when added" on={g.search_on_add} onChange={v => put({ search_on_add: v })} />
        <Rule id={`${p}-unknown`} label="Allow unknown language" on={g.allow_unknown_language} onChange={v => put({ allow_unknown_language: v })} />
        <Rule id={`${p}-plex`} label={`Skip ${kind} in Plex`} on={g.skip_plex} onChange={v => put({ skip_plex: v })} />
        <Rule id={`${p}-unreadable`} label={"Skip the run if Plex can't be read"} on={g.skip_if_plex_unreadable} onChange={v => put({ skip_if_plex_unreadable: v })} />
        {kind === 'shows' && <Rule id={`${p}-tvdb`} label="Skip shows without a TVDB ID" on={!!g.require_tvdb} onChange={v => put({ require_tvdb: v })} />}
        <Rule id={`${p}-readd`} label={`Don't re-add removed ${kind}`} on={g.no_readd} onChange={v => put({ no_readd: v })} />
      </div>
    </section>
  );
}

function StatusPanel({ status, history }: { status: AnStatus; history: AnEntry[] }) {
  const r = status.last_result;
  const state = status.running ? { text: 'Running', color: '#60a5fa' } : status.enabled ? { text: 'Idle', color: '#4ade80' } : { text: 'Off', color: W50 };
  const tiles: [string, string, string][] = [
    ['Last ran', status.last_run ? when(new Date(status.last_run * 1000)) : '—', ''],
    ['Next run', status.next_run ? when(new Date(status.next_run * 1000)) : '—', ''],
    ['Shows', r?.error || counts(r?.shows ?? null), r?.error ? '' : skips(r?.shows ?? null)],
    ['Movies', r?.error || counts(r?.movies ?? null), r?.error ? '' : skips(r?.movies ?? null)],
  ];
  return (
    <section aria-label="Status" data-testid="an-status" style={{ ...panel, padding: '16px 16px 8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: W95 }}>Status</h3>
        <span data-testid="an-state" style={{ fontSize: 12, color: state.color }}>● {state.text}</span>
      </div>
      <div className="gr-f2" style={{ gap: 8, marginBottom: 18 }}>
        {tiles.map(([k, v, sub]) => (
          <div key={k} style={tile} data-testid="an-tile">
            <div style={{ fontSize: 12, color: W70, marginBottom: 4 }}>{k}</div>
            <div style={{ fontSize: 15, color: W95, overflowWrap: 'anywhere' }}>{v}</div>
            {sub && <div data-testid="an-skips" style={{ fontSize: 12, color: W50, marginTop: 4, overflowWrap: 'anywhere' }}>Skipped: {sub}</div>}
          </div>
        ))}
      </div>
      <h4 style={group}>History</h4>
      <div role="list">
        {history.length === 0 && <div style={{ padding: '12px 0', fontSize: 13, color: W50 }}>Nothing added yet</div>}
        {history.map(h => (
          <div role="listitem" key={h.id} data-testid="an-history" style={{ borderTop: '1px solid rgba(255,255,255,0.06)', minHeight: 44, display: 'flex', alignItems: 'center', gap: 10, padding: '0 4px', fontSize: 13 }}>
            <span style={{ flex: 1, minWidth: 0, color: W95, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.title}</span>
            {h.year && <span style={{ color: W50, fontSize: 12, flex: 'none' }}>{h.year}</span>}
            <span style={{ color: W70, fontSize: 12, flex: 'none' }}>{stamp(new Date(h.added_at * 1000))}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

const arr = <T,>(x: T[] | null | undefined): T[] => (Array.isArray(x) ? x : []);
const isObj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x);
const normHistory = (h: AnEntry[] | null | undefined): AnEntry[] => arr(h).filter(isObj);
/** A view missing its settings, either group or its status is a failed load ("Couldn't load"), never a crash;
 * list fields that aren't arrays read as empty (like Genre roulette's normGr). */
export function normAn(v: unknown): AnView | null {
  if (!isObj(v) || !isObj(v.settings) || !isObj(v.status) || !isObj(v.settings.shows) || !isObj(v.settings.movies)) return null;
  const d = v as unknown as AnView;
  const g = (x: AnGroup): AnGroup => ({ ...x, languages: arr(x.languages) });
  return { ...d, profiles: arr(d.profiles), movie_profiles: arr(d.movie_profiles), libraries: arr(d.libraries),
    monitor: arr(d.monitor), languages: arr(d.languages), history: normHistory(d.history),
    settings: { ...d.settings, shows: g(d.settings.shows), movies: g(d.settings.movies) } };
}

export function AnticipatedSettings({ wide = true }: { wide?: boolean } = {}) {
  const [d, setD] = useState<AnView | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState('');
  const busy = useRef(false);

  useEffect(() => { void loadAn().then(r => { const v = r.ok ? normAn(r.data) : null; if (v) setD(v); else setFailed(true); }); }, []);
  const live = useCallback(() => {
    void loadAnLive().then(r => {
      if (!r.ok || !isObj(r.data) || !isObj(r.data.status)) return;
      const { status, history } = r.data;
      setD(cur => (cur ? { ...cur, status, history: normHistory(history) } : cur));
    });
  }, []);
  const fast = !!d?.status.running;
  useEffect(() => {
    if (!d) return undefined;
    const t = window.setInterval(live, fast ? 3000 : 30000);
    return () => window.clearInterval(t);
  }, [!!d, fast, live]); // eslint-disable-line react-hooks/exhaustive-deps

  if (failed) return <div role="alert" style={{ ...panel, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W50 }}>{"Couldn't load"}</div>;
  if (!d) return <div aria-busy="true" style={{ ...panel, height: 120 }} />;
  const s = d.settings;

  const put = (patch: AnPatch) => {
    const before = d;
    setD({ ...d, settings: { ...s, ...patch, shows: { ...s.shows, ...patch.shows }, movies: { ...s.movies, ...patch.movies } } });
    setError('');
    void saveAn(patch).then(r => {
      const v = r.ok ? normAn(r.data) : null;
      if (v) { setD(v); return; }
      setD(before);
      setError(SAVE_ERROR);
    });
  };
  const run = () => {
    if (busy.current) return;
    busy.current = true;
    setError('');
    void runAn().then(r => {
      busy.current = false;
      if (!r.ok && r.status !== 409) setError("Couldn't start");
      live();
    });
  };
  const running = d.status.running;
  const sub = d.status.next_run && d.status.enabled ? `Next run ${when(new Date(d.status.next_run * 1000))}` : '';

  return (
    <div data-testid="anticipated" style={{ display: 'flex', flexDirection: 'column', gap: 16, fontFamily: FONT, color: W95, padding: wide ? 0 : '8px 16px 24px' }}>
      <style>{tvEditCss}{css}</style>
      <section className="gr-head" aria-label="Anticipated" style={{ ...panel, display: 'flex', alignItems: 'center', gap: 16, padding: '12px 16px', minHeight: 64 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 12, rowGap: 2, flex: 1, minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>Anticipated</h3>
          {sub && <span data-testid="an-sub" style={{ fontSize: 12, color: W70 }}>{sub}</span>}
        </div>
        <div className="gr-actions" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="button" className="tve-primary lp-focus" data-testid="an-run" disabled={running} onClick={run}
            style={{ ...primaryBtn(!running), ...pill }}>{running ? 'Running' : 'Run now'}</button>
        </div>
      </section>
      {error && <div role="alert" style={{ fontSize: 13, color: RED }}>{error}</div>}
      <div className="gr-grid">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          <section aria-label="Schedule" style={{ ...panel, padding: 16 }}>
            <div className="gr-f3">
              <Field id="an-every" label="Check every"><Select id="an-every" value={s.interval_hours} options={HOURS} onChange={v => put({ interval_hours: v })} /></Field>
              <Field id="an-busy" label="When Trakt is busy"><Select id="an-busy" value={s.when_busy} options={BUSY} onChange={v => put({ when_busy: v })} /></Field>
              <Field id="an-kept" label="History kept"><Select id="an-kept" value={s.history_kept} options={KEPT} onChange={v => put({ history_kept: v as 50 | 200 | 500 })} /></Field>
            </div>
          </section>
          {(['shows', 'movies'] as Kind[]).map(k => (
            <GroupSettings key={k} kind={k} g={s[k]} d={d} put={p => put({ [k]: p })} />
          ))}
        </div>
        <StatusPanel status={d.status} history={d.history} />
      </div>
    </div>
  );
}
