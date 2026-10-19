/** v181 (Taylor): the Sonarr-style bulk editor (Luma's own code; Sonarr is a behavior reference only).
 * Series list view: a checkbox per show, select all and a selected count; a bottom action bar applies
 * Monitored, Monitor, Monitor new seasons, Quality profile, Series type and Season folder to the selected
 * shows (no root folder or path, no delete), with a confirmation when more than one show is selected.
 * The show sheet: per-episode checkboxes, select all, per-season select, monitor or unmonitor selected.
 * Everything writes tv.db only; nothing here ever searches or grabs. Sentence case, no helper text, 12px floor. */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { EditSeriesModal, Modal, NEW_SEASONS, SERIES_TYPES, Select, footBtn, glassBtn, primaryBtn } from './TvEdit';
import {
  MONITOR_OPTIONS, bulkEditShows, getSeries, setEpisodesMonitored,
  type BulkChanges, type MonitorOption, type Profile, type SeriesType, type SeriesView,
} from './tvCalendarApi';
import type { Show } from './tvLibraryApi';

const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const ACCENT = '#3b82f6';
const RED = '#ef4444';
const LINE = '1px solid rgba(255,255,255,0.06)';
export const BULK_SAVE_ERROR = "Couldn't save";

export const bulkCss = `
.tvb-row:hover{background:rgba(255,255,255,.04)}
.tvb-title:hover{color:#fff!important;text-decoration:underline}
`;

/** A 20px checkbox (true, false or mixed), the Series grid's check. */
export function Check({ state, onChange, label, testid }: {
  state: boolean | 'mixed'; onChange: (on: boolean) => void; label: string; testid?: string;
}) {
  const on = state === true;
  const mixed = state === 'mixed';
  return (
    <button type="button" role="checkbox" aria-checked={mixed ? 'mixed' : on} aria-label={label} data-testid={testid}
      className="lp-focus" onClick={e => { e.stopPropagation(); onChange(!on); }}
      style={{ width: 20, height: 20, padding: 0, flex: 'none', borderRadius: 6, cursor: 'pointer', boxSizing: 'border-box',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: on || mixed ? ACCENT : 'rgba(21,16,42,.6)', border: on || mixed ? 'none' : '1.5px solid rgba(255,255,255,0.6)' }}>
      {on && <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5" /></svg>}
      {mixed && <span aria-hidden="true" style={{ width: 10, height: 2, borderRadius: 1, background: '#fff' }} />}
    </button>
  );
}

const allState = (n: number, total: number): boolean | 'mixed' => (n === 0 ? false : n === total ? true : 'mixed');
export const monitorLabel = (m?: string | null) => MONITOR_OPTIONS.find(o => o.id === m)?.label ?? '';
export const typeLabel = (t?: string | null) => SERIES_TYPES.find(o => o.id === t)?.label ?? '';

// ---------------------------------------------------------------- the list view

export function SeriesList({ shows, profiles, picked, setPicked, onOpen }: {
  shows: Show[]; profiles: Profile[]; picked: Set<number>; setPicked: (p: Set<number>) => void; onOpen: (s: Show) => void;
}) {
  const names = useMemo(() => new Map(profiles.map(p => [p.id, p.name])), [profiles]);
  const toggle = (id: number, on: boolean) => { const n = new Set(picked); if (on) n.add(id); else n.delete(id); setPicked(n); };
  return (
    <div data-testid="tv-series-list" role="table" aria-label="Shows" style={{ ...listCard, fontFamily: FONT }}>
      <style>{bulkCss}</style>
      <div role="row" style={{ ...rowGrid, minHeight: 44, borderBottom: LINE }}>
        <span role="columnheader" style={cellCenter}>
          <Check state={allState(picked.size, shows.length)} label="Select all" testid="tv-list-select-all"
            onChange={on => setPicked(on ? new Set(shows.map(s => s.id)) : new Set())} />
        </span>
        {['Title', 'Monitored', 'Monitor', 'Quality profile', 'Series type', 'Episodes'].map(h => (
          <span key={h} role="columnheader" style={headCell}>{h}</span>
        ))}
      </div>
      {shows.map(s => {
        const on = picked.has(s.id);
        return (
          <div key={s.id} role="row" className="tvb-row" data-testid="tv-list-row" data-show-id={s.id} data-picked={on || undefined}
            onClick={() => toggle(s.id, !on)}
            style={{ ...rowGrid, minHeight: 48, borderTop: LINE, cursor: 'pointer', background: on ? 'rgba(59,130,246,0.10)' : undefined }}>
            <span role="cell" style={cellCenter}><Check state={on} label={s.title} testid="tv-list-check" onChange={v => toggle(s.id, v)} /></span>
            <span role="cell" style={{ ...cell, minWidth: 0 }}>
              <button type="button" className="lp-focus tvb-title" data-testid="tv-list-open" onClick={e => { e.stopPropagation(); onOpen(s); }}
                style={{ padding: 0, border: 'none', background: 'none', cursor: 'pointer', fontFamily: FONT, fontSize: 14, fontWeight: 600, color: W95,
                  textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>
                {s.year ? `${s.title} (${s.year})` : s.title}
              </button>
            </span>
            <span role="cell" data-testid="tv-list-monitored" style={{ ...cell, color: s.monitored ? W95 : W50 }}>{s.monitored ? 'Monitored' : 'Unmonitored'}</span>
            <span role="cell" style={cell}>{monitorLabel(s.monitor)}</span>
            <span role="cell" style={cell}>{s.profile_id != null ? names.get(s.profile_id) ?? '' : ''}</span>
            <span role="cell" style={cell}>{typeLabel(s.series_type)}</span>
            <span role="cell" style={{ ...cell, color: W70 }}>{`${Math.min(s.have ?? 0, s.aired ?? 0)} of ${s.aired ?? 0}`}</span>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- the action bar

const KEEP = '' as const;
type Draft = {
  monitored: '' | 'on' | 'off'; monitor: '' | MonitorOption; monitor_new_seasons: '' | 'all' | 'none';
  profile_id: '' | number; series_type: '' | SeriesType; season_folder: '' | 'yes' | 'no';
};
const EMPTY: Draft = { monitored: KEEP, monitor: KEEP, monitor_new_seasons: KEEP, profile_id: KEEP, series_type: KEEP, season_folder: KEEP };
const NO_CHANGE = { id: KEEP, label: 'No change' };

export function draftChanges(d: Draft): BulkChanges {
  const c: BulkChanges = {};
  if (d.monitored) c.monitored = d.monitored === 'on';
  if (d.monitor) c.monitor = d.monitor;
  if (d.monitor_new_seasons) c.monitor_new_seasons = d.monitor_new_seasons;
  if (d.profile_id !== KEEP) c.profile_id = d.profile_id;
  if (d.series_type) c.series_type = d.series_type;
  if (d.season_folder) c.season_folder = d.season_folder === 'yes';
  return c;
}

/** The changes, in words, for the confirmation ("Monitor: All episodes"). */
export function changeLines(c: BulkChanges, profiles: Profile[]): [string, string][] {
  const out: [string, string][] = [];
  if (c.monitored !== undefined) out.push(['Monitored', c.monitored ? 'On' : 'Off']);
  if (c.monitor) out.push(['Monitor', monitorLabel(c.monitor)]);
  if (c.monitor_new_seasons) out.push(['Monitor new seasons', NEW_SEASONS.find(o => o.id === c.monitor_new_seasons)?.label ?? '']);
  if (c.profile_id !== undefined) out.push(['Quality profile', profiles.find(p => p.id === c.profile_id)?.name ?? '']);
  if (c.series_type) out.push(['Series type', typeLabel(c.series_type)]);
  if (c.season_folder !== undefined) out.push(['Season folder', c.season_folder ? 'Yes' : 'No']);
  return out;
}

export function BulkBar({ ids, profiles, onClear, onDone }: {
  ids: number[]; profiles: Profile[]; onClear: () => void; onDone: (updated: number) => void;
}) {
  const [d, setD] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState(false);
  const changes = draftChanges(d);
  const dirty = Object.keys(changes).length > 0;
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD(x => ({ ...x, [k]: v }));
  const run = async () => {
    setConfirm(false); setBusy(true); setError('');
    const r = await bulkEditShows(ids, changes);
    setBusy(false);
    if (!r.ok) { setError(BULK_SAVE_ERROR); return; }
    setD(EMPTY);
    onDone(r.data.updated);
  };
  const apply = () => { if (!dirty || busy || !ids.length) return; if (ids.length > 1) setConfirm(true); else void run(); };
  // fixed to the bottom of the viewport over the list's column (the app's body is the scroll container, so
  // position: sticky does not pin here); a spacer in the flow keeps the last rows clear of it
  const slot = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState<{ left: number; width: number; h: number }>({ left: 0, width: 0, h: 0 });
  useLayoutEffect(() => {
    const place = () => {
      const r = slot.current?.getBoundingClientRect();
      if (!r) return;
      const h = bar.current?.offsetHeight ?? 0;
      setBox(b => (b.left === r.left && b.width === r.width && b.h === h ? b : { left: r.left, width: r.width, h }));
    };
    place();
    window.addEventListener('resize', place);
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(place);
    if (ro && slot.current) ro.observe(slot.current);
    if (ro && bar.current) ro.observe(bar.current);
    return () => { window.removeEventListener('resize', place); ro?.disconnect(); };
  }, []);
  const cols = box.width >= 1040 ? 6 : 3;
  const field = (id: string, label: string, el: React.ReactNode) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
      <span id={`${id}-label`} style={{ fontSize: 12, fontWeight: 600, color: W50 }}>{label}</span>
      {el}
    </div>
  );
  const n = ids.length;
  return (
    <div ref={slot} data-testid="tv-bulk-slot" style={{ height: box.h + 16, flex: 'none' }}>
    <div ref={bar} data-testid="tv-bulk-bar" role="region" aria-label="Edit selected shows"
      style={{ position: 'fixed', bottom: 16, left: box.left, width: box.width || undefined, zIndex: 20, padding: 16, borderRadius: 16, boxSizing: 'border-box', fontFamily: FONT,
        background: 'rgba(21,16,42,0.94)', border: '1px solid rgba(255,255,255,0.10)', backdropFilter: 'blur(24px)', WebkitBackdropFilter: 'blur(24px)',
        boxShadow: '0 16px 48px rgba(0,0,0,0.5)', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, minHeight: 40 }}>
      <span data-testid="tv-bulk-count" style={{ fontSize: 14, fontWeight: 600, color: W95, flex: 1 }}>{`${n} selected`}</span>
      {error && <span role="alert" data-testid="tv-bulk-error" style={{ fontSize: 13, color: RED }}>{error}</span>}
      <button type="button" className="lp-focus tve-glass" data-testid="tv-bulk-clear" onClick={onClear} style={glassBtn}>Clear</button>
      <button type="button" className="lp-focus tve-primary" data-testid="tv-bulk-apply" disabled={!dirty || busy}
        onClick={apply} style={primaryBtn(dirty && !busy)}>Apply</button>
      </div>
      <div data-testid="tv-bulk-fields" style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gap: 12 }}>
      {field('tvb-monitored', 'Monitored', <Select up id="tvb-monitored" value={d.monitored}
        options={[NO_CHANGE, { id: 'on' as const, label: 'On' }, { id: 'off' as const, label: 'Off' }]} onChange={v => set('monitored', v)} />)}
      {field('tvb-monitor', 'Monitor', <Select up id="tvb-monitor" value={d.monitor}
        options={[NO_CHANGE, ...MONITOR_OPTIONS]} onChange={v => set('monitor', v)} />)}
      {field('tvb-new-seasons', 'Monitor new seasons', <Select up id="tvb-new-seasons" value={d.monitor_new_seasons}
        options={[NO_CHANGE, ...NEW_SEASONS]} onChange={v => set('monitor_new_seasons', v)} />)}
      {field('tvb-profile', 'Quality profile', <Select<'' | number> up id="tvb-profile" value={d.profile_id}
        options={[NO_CHANGE, ...profiles.map(p => ({ id: p.id, label: p.name }))]} onChange={v => set('profile_id', v)} />)}
      {field('tvb-type', 'Series type', <Select up id="tvb-type" value={d.series_type}
        options={[NO_CHANGE, ...SERIES_TYPES]} onChange={v => set('series_type', v)} />)}
      {field('tvb-season-folder', 'Season folder', <Select up id="tvb-season-folder" value={d.season_folder}
        options={[NO_CHANGE, { id: 'yes' as const, label: 'Yes' }, { id: 'no' as const, label: 'No' }]} onChange={v => set('season_folder', v)} />)}
      </div>
    </div>
      {/* outside the bar: its backdrop-filter would contain the dialog's fixed overlay */}
      {confirm && (
        <Modal title={`Apply to ${n} shows?`} testid="tv-bulk-confirm" onClose={() => setConfirm(false)} footer={(
          <>
            <span style={{ flex: 1 }} />
            <button type="button" className="lp-focus tve-glass" data-testid="tv-bulk-confirm-cancel" onClick={() => setConfirm(false)} style={glassBtn}>Cancel</button>
            <button type="button" className="lp-focus tve-primary" data-testid="tv-bulk-confirm-apply" onClick={() => void run()} style={primaryBtn(true)}>
              {`Apply to ${n} shows`}
            </button>
          </>
        )}>
          <div data-testid="tv-bulk-confirm-changes" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {changeLines(changes, profiles).map(([k, v]) => (
              <div key={k} style={{ display: 'grid', gridTemplateColumns: '180px minmax(0, 1fr)', gap: 16, fontSize: 14 }}>
                <span style={{ color: W70 }}>{k}</span><span style={{ color: W95, fontWeight: 600 }}>{v}</span>
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- the show sheet + episode select

type Ep = SeriesView['episodes'][number];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** "Oct 3, 2024" from a 'YYYY-MM-DD' air date (a calendar date: no zone shift); '' when unknown. */
export function airText(d?: string | null): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d || '');
  return m ? `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}` : '';
}
export const seasonName = (n: number) => (n === 0 ? 'Specials' : `Season ${n}`);
/** Seasons newest first, specials last. */
export const seasonOrder = (eps: Ep[]) =>
  [...new Set(eps.map(e => e.season))].sort((a, b) => (a === 0 ? 1 : b === 0 ? -1 : b - a));

export function ShowSheet({ seriesId, title, onClose, onChanged }: {
  seriesId: number; title: string; onClose: () => void; onChanged?: () => void;
}) {
  const [view, setView] = useState<SeriesView | null>(null);
  const [failed, setFailed] = useState(false);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    let live = true;
    void getSeries(seriesId).then(r => { if (!live) return; if (r.ok) setView(r.data); else setFailed(true); });
    return () => { live = false; };
  }, [seriesId]);
  const eps = view?.episodes ?? [];
  const pick = (ids: number[], on: boolean) => setPicked(p => { const n = new Set(p); ids.forEach(i => (on ? n.add(i) : n.delete(i))); return n; });
  const mark = async (on: boolean) => {
    if (!picked.size || busy) return;
    setBusy(true); setError('');
    const r = await setEpisodesMonitored(seriesId, [...picked], on);
    setBusy(false);
    if (!r.ok) { setError(BULK_SAVE_ERROR); return; }
    setView(r.data); setPicked(new Set()); onChanged?.();
  };
  const actions = (
    <button type="button" className="lp-focus tve-glass" data-testid="tv-sheet-edit" onClick={() => setEditing(true)} style={{ ...glassBtn, height: 36 }}>Edit</button>
  );
  const footer = (
    <>
      <Check state={allState(picked.size, eps.length)} label="Select all" testid="tv-ep-select-all"
        onChange={on => setPicked(on ? new Set(eps.map(e => e.id)) : new Set())} />
      <span data-testid="tv-ep-count" style={{ fontSize: 14, fontWeight: 600, color: W95 }}>{`${picked.size} selected`}</span>
      {error && <span role="alert" style={{ fontSize: 13, color: RED }}>{error}</span>}
      <span style={{ flex: 1 }} />
      <button type="button" className="lp-focus tve-glass" data-testid="tv-ep-unmonitor" disabled={!picked.size || busy} onClick={() => void mark(false)}
        style={{ ...glassBtn, opacity: picked.size && !busy ? 1 : 0.4, cursor: picked.size ? 'pointer' : 'default' }}>Unmonitor</button>
      <button type="button" className="lp-focus tve-primary" data-testid="tv-ep-monitor" disabled={!picked.size || busy} onClick={() => void mark(true)}
        style={primaryBtn(!!picked.size && !busy)}>Monitor</button>
    </>
  );
  return (
    <>
      <Modal title={title} testid="tv-show-sheet" width={760} onClose={onClose} actions={actions} footer={footer}>
        <style>{bulkCss}</style>
        {failed ? (
          <div role="alert" style={{ fontSize: 14, color: W70 }}>{"Couldn't load"}</div>
        ) : !view ? (
          <div aria-busy="true" style={{ height: 240 }} />
        ) : !eps.length ? (
          <div data-testid="tv-ep-empty" style={{ fontSize: 14, color: W70 }}>No episodes</div>
        ) : seasonOrder(eps).map(n => {
          const list = eps.filter(e => e.season === n);
          const on = list.filter(e => picked.has(e.id)).length;
          const mon = list.filter(e => e.monitored).length;
          return (
            <section key={n} data-testid="tv-ep-season" data-season={n} style={{ borderRadius: 12, border: LINE, overflow: 'hidden' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, minHeight: 44, padding: '0 12px', background: 'rgba(255,255,255,0.04)' }}>
                <Check state={allState(on, list.length)} label={`Select ${seasonName(n)}`} testid="tv-ep-season-check"
                  onChange={v => pick(list.map(e => e.id), v)} />
                <span style={{ fontSize: 14, fontWeight: 600, color: W95, flex: 1 }}>{seasonName(n)}</span>
                <span data-testid="tv-ep-season-monitored" style={{ fontSize: 12, color: W50 }}>{`${mon} of ${list.length} monitored`}</span>
              </div>
              {list.map(e => {
                const sel = picked.has(e.id);
                return (
                  <div key={e.id} className="tvb-row" data-testid="tv-ep-row" data-episode-id={e.id} data-monitored={e.monitored || undefined}
                    onClick={() => pick([e.id], !sel)}
                    style={{ display: 'grid', gridTemplateColumns: '20px 40px minmax(0, 1fr) 110px 96px', alignItems: 'center', columnGap: 12,
                      minHeight: 44, padding: '0 12px', borderTop: LINE, cursor: 'pointer', background: sel ? 'rgba(59,130,246,0.10)' : undefined,
                      boxShadow: e.monitored ? `inset 4px 0 0 ${ACCENT}` : undefined }}>
                    <Check state={sel} label={`${seasonName(n)} episode ${e.episode}`} testid="tv-ep-check" onChange={v => pick([e.id], v)} />
                    <span style={{ fontSize: 13, color: W50, fontVariantNumeric: 'tabular-nums' }}>{e.episode}</span>
                    <span style={{ fontSize: 14, color: W95, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.title || `Episode ${e.episode}`}</span>
                    <span style={{ fontSize: 13, color: W70 }}>{airText(e.air_date)}</span>
                    <span style={{ fontSize: 12, color: e.monitored ? W95 : W50, textAlign: 'right' }}>{e.monitored ? 'Monitored' : 'Unmonitored'}</span>
                  </div>
                );
              })}
            </section>
          );
        })}
      </Modal>
      {editing && (
        <EditSeriesModal seriesId={seriesId} title={title} onClose={() => setEditing(false)}
          onSaved={v => { setView(v); onChanged?.(); }} onDeleted={() => { onChanged?.(); onClose(); }} />
      )}
    </>
  );
}

const listCard: React.CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' };
const rowGrid: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: '44px minmax(200px, 2.4fr) minmax(110px, 1fr) minmax(140px, 1.2fr) minmax(120px, 1fr) minmax(100px, 0.9fr) minmax(90px, 0.8fr)',
  alignItems: 'center', columnGap: 12, paddingRight: 16, boxSizing: 'border-box',
};
const cell: React.CSSProperties = { fontSize: 13, color: W95, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
const cellCenter: React.CSSProperties = { display: 'flex', alignItems: 'center', justifyContent: 'center' };
const headCell: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: W50 };
export { footBtn };
