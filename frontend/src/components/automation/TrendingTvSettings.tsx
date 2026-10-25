/** v279 Settings > Automation > Trending TV (prod's trending TV watchlist, native): every few hours Trakt's
 * trending shows are added through TV Add (tv.db). Same layout as Anticipated / Genre roulette. Owner only;
 * changes and Run now need the signed-in owner. Settings save as they change. */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Select, Toggle, SAVE_ERROR, primaryBtn, tvEditCss } from '@/components/library/TvEdit';
import { ADD_MONITOR, type MonitorOption } from '@/components/library/tvCalendarApi';
import { Field, LANGUAGES, NumField, Stepper, stamp, when } from './GenreRouletteSettings';
import { loadTtv, loadTtvLive, runTtv, saveTtv, type TtvCounts, type TtvEntry, type TtvPatch, type TtvStatus, type TtvView } from './trendingTvApi';

const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const RED = '#fca5a5';
const panel: CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', boxSizing: 'border-box' };
const group: CSSProperties = { margin: '0 0 8px', fontSize: 11, fontWeight: 700, letterSpacing: '0.02em', color: '#a5b4fc' };
const tile: CSSProperties = { borderRadius: 12, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.06)', padding: '10px 12px', minWidth: 0 };
const pill: CSSProperties = { borderRadius: 20, height: 40, padding: '0 20px' };
const rule: CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, minHeight: 40 };

const css = '@media (max-width:599px){.ttv-rules{grid-template-columns:minmax(0,1fr)!important}}';
const HOURS = Array.from({ length: 24 }, (_, i) => ({ id: i + 1, label: i ? `${i + 1} hours` : '1 hour' }));
const BUSY = [{ id: 'skip' as const, label: 'Skip this run' }, { id: 'retry' as const, label: 'Retry in 15 min' }];
const KEPT = [50, 200, 500].map(n => ({ id: n, label: String(n) }));
export const SKIPPED: Record<string, string> = {
  existing: 'already added', plex: 'in Plex', language: 'language', no_tvdb: 'no TVDB ID', no_tmdb: 'no TMDB ID',
  removed: 'removed before', seasons: 'not 1 season',
};

export const counts = (c: TtvCounts | null) => (!c ? '—' : c.error ? c.error : `Added ${c.added} of ${c.checked}`);
export const skips = (c: TtvCounts | null) =>
  (!c ? '' : Object.entries(c.skipped && typeof c.skipped === 'object' ? c.skipped : {}).filter(([, n]) => n > 0).map(([k, n]) => `${SKIPPED[k] || k} ${n}`).join(', '));

function Lang({ code, on, onClick }: { code: string; on: boolean; onClick: () => void }) {
  const s: CSSProperties = on
    ? { background: 'rgba(110,134,180,0.38)', borderColor: 'rgba(140,165,215,0.45)', color: W95 }
    : { background: 'none', borderColor: 'rgba(255,255,255,0.14)', color: W95 };
  return (
    <button type="button" className="gr-chip lp-focus" data-testid="ttv-lang" data-code={code} aria-pressed={on} onClick={onClick}
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

function StatusPanel({ status, history }: { status: TtvStatus; history: TtvEntry[] }) {
  const r = status.last_result;
  const state = status.running ? { text: 'Running', color: '#60a5fa' } : status.enabled ? { text: 'Idle', color: '#4ade80' } : { text: 'Off', color: W50 };
  const tiles: [string, string, string][] = [
    ['Last ran', status.last_run ? when(new Date(status.last_run * 1000)) : '—', ''],
    ['Next run', status.next_run ? when(new Date(status.next_run * 1000)) : '—', ''],
    ['Shows', r?.error || counts(r), r?.error ? '' : skips(r)],
  ];
  return (
    <section aria-label="Status" data-testid="ttv-status" style={{ ...panel, padding: '16px 16px 8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: W95 }}>Status</h3>
        <span data-testid="ttv-state" style={{ fontSize: 12, color: state.color }}>● {state.text}</span>
      </div>
      <div className="gr-f2" style={{ gap: 8, marginBottom: 18 }}>
        {tiles.map(([k, v, sub]) => (
          <div key={k} style={tile} data-testid="ttv-tile">
            <div style={{ fontSize: 12, color: W70, marginBottom: 4 }}>{k}</div>
            <div style={{ fontSize: 15, color: W95, overflowWrap: 'anywhere' }}>{v}</div>
            {sub && <div data-testid="ttv-skips" style={{ fontSize: 12, color: W50, marginTop: 4, overflowWrap: 'anywhere' }}>Skipped: {sub}</div>}
          </div>
        ))}
      </div>
      <h4 style={group}>History</h4>
      <div role="list">
        {history.length === 0 && <div style={{ padding: '12px 0', fontSize: 13, color: W50 }}>Nothing added yet</div>}
        {history.map(h => (
          <div role="listitem" key={h.id} data-testid="ttv-history" style={{ borderTop: '1px solid rgba(255,255,255,0.06)', minHeight: 44, display: 'flex', alignItems: 'center', gap: 10, padding: '0 4px', fontSize: 13 }}>
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
const normHistory = (h: TtvEntry[] | null | undefined): TtvEntry[] => arr(h).filter(isObj);

export function normTtv(v: unknown): TtvView | null {
  if (!isObj(v) || !isObj(v.settings) || !isObj(v.status)) return null;
  const d = v as unknown as TtvView;
  return { ...d, profiles: arr(d.profiles), libraries: arr(d.libraries), monitor: arr(d.monitor),
    languages: arr(d.languages), history: normHistory(d.history),
    settings: { ...d.settings, languages: arr(d.settings.languages) } };
}

export function TrendingTvSettings({ wide = true }: { wide?: boolean } = {}) {
  const [d, setD] = useState<TtvView | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState('');
  const busy = useRef(false);

  useEffect(() => { void loadTtv().then(r => { const v = r.ok ? normTtv(r.data) : null; if (v) setD(v); else setFailed(true); }); }, []);
  const live = useCallback(() => {
    void loadTtvLive().then(r => {
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

  const put = (patch: TtvPatch) => {
    const before = d;
    setD({ ...d, settings: { ...s, ...patch } });
    setError('');
    void saveTtv(patch).then(r => {
      const v = r.ok ? normTtv(r.data) : null;
      if (v) { setD(v); return; }
      setD(before);
      setError(SAVE_ERROR);
    });
  };
  const run = () => {
    if (busy.current) return;
    busy.current = true;
    setError('');
    void runTtv().then(r => {
      busy.current = false;
      if (!r.ok && r.status !== 409) setError("Couldn't start");
      live();
    });
  };
  const running = d.status.running;
  const sub = d.status.next_run && d.status.enabled ? `Next run ${when(new Date(d.status.next_run * 1000))}` : '';
  const profileId = s.profile_id ?? d.profiles.find(x => x.default)?.id ?? d.profiles[0]?.id ?? null;
  const pathId = s.path_id ?? d.libraries[0]?.id ?? null;
  const flip = (code: string) => put({ languages: s.languages.includes(code) ? s.languages.filter(x => x !== code) : [...s.languages, code] });

  return (
    <div data-testid="trending-tv" style={{ display: 'flex', flexDirection: 'column', gap: 16, fontFamily: FONT, color: W95, padding: wide ? 0 : '8px 16px 24px' }}>
      <style>{tvEditCss}{css}</style>
      <section className="gr-head" aria-label="Trending TV" style={{ ...panel, display: 'flex', alignItems: 'center', gap: 16, padding: '12px 16px', minHeight: 64 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, minWidth: 0 }}>
          <Toggle id="ttv-enabled" on={s.enabled} onChange={v => put({ enabled: v })} label="Trending TV" />
          <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 12, rowGap: 2, minWidth: 0 }}>
            <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>Trending TV</h3>
            {sub && <span data-testid="ttv-sub" style={{ fontSize: 12, color: W70 }}>{sub}</span>}
          </div>
        </div>
        <div className="gr-actions" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="button" className="tve-primary lp-focus" data-testid="ttv-run" disabled={running} onClick={run}
            style={{ ...primaryBtn(!running), ...pill }}>{running ? 'Running' : 'Run now'}</button>
        </div>
      </section>
      {error && <div role="alert" style={{ fontSize: 13, color: RED }}>{error}</div>}
      <div className="gr-grid">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          <section aria-label="Schedule" style={{ ...panel, padding: 16 }}>
            <div className="gr-f3">
              <Field id="ttv-every" label="Check every"><Select id="ttv-every" value={s.interval_hours} options={HOURS} onChange={v => put({ interval_hours: v })} /></Field>
              <Field id="ttv-busy" label="When Trakt is busy"><Select id="ttv-busy" value={s.when_busy} options={BUSY} onChange={v => put({ when_busy: v })} /></Field>
              <Field id="ttv-kept" label="History kept"><Select id="ttv-kept" value={s.history_kept} options={KEPT} onChange={v => put({ history_kept: v as 50 | 200 | 500 })} /></Field>
            </div>
          </section>
          <section aria-label="Shows" data-testid="ttv-shows" style={{ ...panel, padding: 16, display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div className="gr-f3">
              <Field id="ttv-limit" label="Shows per run"><Stepper id="ttv-limit" value={s.limit} min={1} max={40} onChange={v => put({ limit: v })} /></Field>
              <Field id="ttv-profile" label="Quality profile"><Select id="ttv-profile" value={profileId} options={d.profiles.map(x => ({ id: x.id, label: x.name }))} onChange={v => put({ profile_id: v })} /></Field>
              <Field id="ttv-page" label="List page"><NumField id="ttv-page" value={s.page} onCommit={v => put({ page: v })} /></Field>
              <Field id="ttv-library" label="Library"><Select id="ttv-library" value={pathId} options={d.libraries.map(x => ({ id: x.id, label: x.name }))} onChange={v => put({ path_id: v })} /></Field>
              <Field id="ttv-monitor" label="Monitor"><Select id="ttv-monitor" value={s.monitor ?? 'all'} options={ADD_MONITOR} onChange={v => put({ monitor: v as MonitorOption })} /></Field>
            </div>
            <div>
              <h4 style={group}>Original languages</h4>
              <div role="group" aria-label="Original languages" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {d.languages.map(c => <Lang key={c} code={c} on={s.languages.includes(c)} onClick={() => flip(c)} />)}
              </div>
            </div>
            <div className="gr-f2 ttv-rules" style={{ columnGap: 24, rowGap: 0 }}>
              <Rule id="ttv-search" label="Search when added" on={s.search_on_add} onChange={v => put({ search_on_add: v })} />
              <Rule id="ttv-one-season" label="One season only" on={s.one_season_only} onChange={v => put({ one_season_only: v })} />
              <Rule id="ttv-unknown" label="Allow unknown language" on={s.allow_unknown_language} onChange={v => put({ allow_unknown_language: v })} />
              <Rule id="ttv-plex" label="Skip shows in Plex" on={s.skip_plex} onChange={v => put({ skip_plex: v })} />
              <Rule id="ttv-unreadable" label={"Skip the run if Plex can't be read"} on={s.skip_if_plex_unreadable} onChange={v => put({ skip_if_plex_unreadable: v })} />
              <Rule id="ttv-tvdb" label="Skip shows without a TVDB ID" on={s.require_tvdb} onChange={v => put({ require_tvdb: v })} />
              <Rule id="ttv-readd" label="Don't re-add removed shows" on={s.no_readd} onChange={v => put({ no_readd: v })} />
            </div>
          </section>
        </div>
        <StatusPanel status={d.status} history={d.history} />
      </div>
    </div>
  );
}
