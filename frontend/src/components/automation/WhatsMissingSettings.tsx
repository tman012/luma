/** v254 Settings > Automation > What you're missing: the Home rail of popular films the library doesn't have
 * (app/daily_missing). Every value prod 1b4ea74 hard-codes is a setting here, prod's value as the default; whether
 * the rail shows, and where, is Settings > Home screen's. Owner only; changes need the signed-in owner. Settings
 * save as they change; a filter change rebuilds the pool in the background (the rail applies it at once). */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Select, Toggle, SAVE_ERROR, glassBtn, tvEditCss } from '@/components/library/TvEdit';
import { Chip, Field, NumField, Poster, grCss, when, type ChipState } from './GenreRouletteSettings';
import {
  loadWym, loadWymStatus, normalizeStatus, refreshWym, restoreWym, saveWym,
  type Opt, type WymSettings, type WymStatus, type WymView,
} from './whatsMissingApi';

const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const RED = '#fca5a5';
const ACCENT = '#3b82f6';
const panel: CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', boxSizing: 'border-box' };
const group: CSSProperties = { margin: '0 0 8px', fontSize: 11, fontWeight: 700, letterSpacing: '0.02em', color: '#a5b4fc' };
const tile: CSSProperties = { borderRadius: 12, background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.06)', padding: '10px 12px', minWidth: 0 };
const pill: CSSProperties = { borderRadius: 20, height: 40, padding: '0 20px' };
const css = `
.wym-checks{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 16px}
.wym-check:hover .wym-box{border-color:rgba(255,255,255,.45)!important}
@media (max-width:599px){.wym-checks{grid-template-columns:minmax(0,1fr)}}
`;

export const hoursLabel = (h: number) => (h === 24 ? '24 hours' : `${h} hours`);
export const daysLabel = (n: number) => (n === 0 ? 'Off' : n === 1 ? '1 day' : `${n} days`);
export const count = (n: number) => n.toLocaleString('en-US');

/** Latest year: 'Current year' (the default; prod's fixed 2025 hid every newer film) or a fixed year. */
export function yearOptions(now = new Date()): Opt[] {
  const y = now.getFullYear();
  return [{ id: 'current', label: 'Current year' }, ...Array.from({ length: y - 1949 }, (_, i) => ({ id: String(y - i), label: String(y - i) }))];
}

function Check({ id, label, on, onChange }: { id: string; label: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" role="checkbox" aria-checked={on} className="wym-check lp-focus" data-testid={`${id}-check`} onClick={() => onChange(!on)}
      style={{ display: 'flex', alignItems: 'center', gap: 10, minHeight: 36, padding: '0 2px', background: 'none', border: 'none', color: W95,
        fontFamily: FONT, fontSize: 13, cursor: 'pointer', textAlign: 'left' }}>
      <span className="wym-box" aria-hidden="true" style={{ width: 18, height: 18, flex: 'none', borderRadius: 5, boxSizing: 'border-box',
        border: `1.5px solid ${on ? ACCENT : 'rgba(255,255,255,0.28)'}`, background: on ? ACCENT : 'none',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
        {on && <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>}
      </span>
      {label}
    </button>
  );
}

function Switch({ id, label, on, onChange }: { id: string; label: string; on: boolean; onChange: (v: boolean) => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, height: 40 }}>
      <span id={`${id}-label`} style={{ fontSize: 13, color: W95 }}>{label}</span>
      <Toggle id={id} on={on} onChange={onChange} />
    </div>
  );
}

function stateText(s: WymStatus): { text: string; color: string } {
  if (s.building) return { text: 'Rebuilding', color: '#60a5fa' };
  if (!s.on_home) return { text: 'Hidden', color: W50 };
  if (s.error) return { text: s.error, color: RED };
  return { text: 'Idle', color: '#4ade80' };
}

function StatusPanel({ status }: { status: WymStatus }) {
  const st = stateText(status);
  const tiles: [string, string][] = [
    ['Last rebuilt', status.last_rebuilt ? when(new Date(status.last_rebuilt)) : '—'],
    ['Pool size', count(status.pool_size)],
    ['Next rebuild', status.building ? 'Now' : status.next_rebuild ? when(new Date(status.next_rebuild)) : '—'],
  ];
  return (
    <section aria-label="Status" data-testid="wym-status" style={{ ...panel, padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, gap: 12 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: W95 }}>Status</h3>
        <span data-testid="wym-state" style={{ fontSize: 12, color: st.color, textAlign: 'right' }}>● {st.text}</span>
      </div>
      <div className="gr-f2" style={{ gap: 8 }}>
        {tiles.map(([k, v]) => (
          <div key={k} style={tile} data-testid="wym-tile">
            <div style={{ fontSize: 12, color: W70, marginBottom: 4 }}>{k}</div>
            <div style={{ fontSize: 15, color: W95, overflowWrap: 'anywhere' }}>{v}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Dismissed({ items, onRestore, busy }: { items: WymView['dismissed']; onRestore: (id: number) => void; busy: number | null }) {
  return (
    <section aria-label="Dismissed movies" data-testid="wym-dismissed" style={{ ...panel, padding: '16px 16px 8px' }}>
      <h3 style={{ margin: '0 0 10px', fontSize: 16, fontWeight: 600, color: W95 }}>Dismissed movies</h3>
      <div role="list">
        {items.length === 0 && <div style={{ padding: '8px 0 12px', fontSize: 13, color: W50 }}>None</div>}
        {items.map(m => (
          <div role="listitem" key={m.tmdb_id} data-testid="wym-dismissed-row"
            style={{ display: 'flex', alignItems: 'center', gap: 10, minHeight: 48, borderTop: '1px solid rgba(255,255,255,0.06)', minWidth: 0 }}>
            <Poster path={m.poster_path} title={m.title} />
            <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: W95, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {m.title || `TMDb ${m.tmdb_id}`}{m.year ? <span style={{ color: W50 }}> ({m.year})</span> : null}
            </span>
            <button type="button" className="tve-glass lp-focus" data-testid="wym-restore" disabled={busy === m.tmdb_id} onClick={() => onRestore(m.tmdb_id)}
              aria-label={`Restore ${m.title || m.tmdb_id}`} style={{ ...glassBtn, height: 32, padding: '0 14px', borderRadius: 16, fontSize: 13 }}>Restore</button>
          </div>
        ))}
      </div>
    </section>
  );
}

export function WhatsMissingSettings({ wide = true }: { wide?: boolean } = {}) {
  const [d, setD] = useState<WymView | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState('');
  const [bad, setBad] = useState<Record<string, string>>({});
  const [restoring, setRestoring] = useState<number | null>(null);
  const busy = useRef(false);

  useEffect(() => {
    void loadWym().then(r => { if (r.ok) setD(r.data); else setFailed(true); });
  }, []);
  const live = useCallback(() => {
    void loadWymStatus().then(r => { if (r.ok) setD(cur => (cur ? { ...cur, status: normalizeStatus(r.data?.status, cur.status) } : cur)); });
  }, []);
  const fast = !!d?.status.building;
  useEffect(() => {
    if (!d) return undefined;
    const t = window.setInterval(live, fast ? 3000 : 30000);
    return () => window.clearInterval(t);
  }, [!!d, fast, live]); // eslint-disable-line react-hooks/exhaustive-deps

  if (failed) return <div role="alert" style={{ ...panel, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W50 }}>{"Couldn't load"}</div>;
  if (!d) return <div aria-busy="true" style={{ ...panel, height: 120 }} />;
  const s = d.settings;

  const put = (patch: Partial<WymSettings>) => {
    const before = d;
    setD({ ...d, settings: { ...s, ...patch } });
    setError('');
    setBad(b => { const n = { ...b }; Object.keys(patch).forEach(k => delete n[k]); return n; });
    void saveWym(patch).then(r => {
      if (r.ok) { setD(cur => (cur ? { ...r.data, dismissed: cur.dismissed } : r.data)); return; }
      setD(before);
      if (r.errors) setBad(r.errors);
      setError(SAVE_ERROR);
    });
  };
  const refresh = () => {
    if (busy.current) return;
    busy.current = true;
    setError('');
    void refreshWym().then(r => {
      busy.current = false;
      if (r.ok) setD(cur => (cur ? { ...cur, status: normalizeStatus(r.data?.status, cur.status) } : cur));
      else if (r.status !== 409) setError("Couldn't start");
      live();
    });
  };
  const restore = (id: number) => {
    setRestoring(id);
    void restoreWym(id).then(r => {
      setRestoring(null);
      if (r.ok) setD(cur => (cur ? { ...cur, dismissed: Array.isArray(r.data?.dismissed) ? r.data.dismissed : cur.dismissed } : cur));
      else setError("Couldn't restore");
    });
  };
  const toggleIn = (list: string[], id: string, on: boolean, order: string[]) =>
    order.filter(x => (x === id ? on : list.includes(x)));
  const langOrder = d.languages.map(l => l.id);
  const genreState = (g: string): ChipState => (s.exclude_genres.includes(g) ? 'out' : 'on');
  const flipGenre = (g: string) => put({ exclude_genres: s.exclude_genres.includes(g) ? s.exclude_genres.filter(x => x !== g) : d.genres.filter(x => x === g || s.exclude_genres.includes(x)) });
  const flipLang = (id: string) => {
    const on = !s.languages.includes(id);
    if (!on && s.languages.length === 1) return;          // at least one
    put({ languages: toggleIn(s.languages, id, on, langOrder) });
  };
  const building = d.status.building;
  const sub = [d.status.pool_size ? `${count(d.status.pool_size)} movies` : null,
    d.status.next_rebuild && !building ? `Next rebuild ${when(new Date(d.status.next_rebuild))}` : null].filter(Boolean).join(' · ');

  return (
    <div data-testid="whats-missing" style={{ display: 'flex', flexDirection: 'column', gap: 16, fontFamily: FONT, color: W95, padding: wide ? 0 : '8px 16px 24px' }}>
      <style>{tvEditCss}{grCss}{css}</style>
      <section className="gr-head" aria-label="What you're missing" style={{ ...panel, display: 'flex', alignItems: 'center', gap: 16, padding: '12px 16px', minHeight: 64 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 12, rowGap: 2, flex: 1, minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>What you&apos;re missing</h3>
          {sub && <span data-testid="wym-sub" style={{ fontSize: 12, color: W70 }}>{sub}</span>}
        </div>
        <div className="gr-actions" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="button" className="tve-glass lp-focus" data-testid="wym-refresh" disabled={building} onClick={refresh}
            style={{ ...glassBtn, ...pill, opacity: building ? 0.5 : 1 }}>{building ? 'Rebuilding' : 'Refresh now'}</button>
        </div>
      </section>
      {error && <div role="alert" style={{ fontSize: 13, color: RED }}>{error}</div>}

      <div className="gr-grid">
        <section aria-label="Settings" style={{ ...panel, padding: 16, display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div>
            <h4 style={group}>Rail</h4>
            <div className="gr-f3">
              <Field id="wym-movies" label="Movies shown"><NumField id="wym-movies" value={s.movies_shown} bad={!!bad.movies_shown} onCommit={v => put({ movies_shown: Math.max(d.movies_min, Math.min(d.movies_max, v)) })} /></Field>
              <Field id="wym-rebuild" label="Rebuild every"><Select id="wym-rebuild" value={s.rebuild_hours} options={d.rebuild_hours.map(h => ({ id: h, label: hoursLabel(h) }))} onChange={v => put({ rebuild_hours: v })} /></Field>
              <Field id="wym-repeat" label="Don't repeat for"><Select id="wym-repeat" value={s.no_repeat_days} options={Array.from({ length: d.no_repeat_max + 1 }, (_, n) => ({ id: n, label: daysLabel(n) }))} onChange={v => put({ no_repeat_days: v })} /></Field>
            </div>
          </div>
          <div>
            <h4 style={group}>Years</h4>
            <div className="gr-f2">
              <Field id="wym-year-from" label="Earliest year"><NumField id="wym-year-from" value={s.year_from} bad={!!bad.year_from} onCommit={v => put({ year_from: v })} /></Field>
              <Field id="wym-year-to" label="Latest year"><Select id="wym-year-to" value={s.year_to == null ? 'current' : String(s.year_to)} options={yearOptions()} onChange={v => put({ year_to: v === 'current' ? null : Number(v) })} /></Field>
            </div>
          </div>
          <div>
            <h4 style={group}>Original languages</h4>
            <div role="group" aria-label="Original languages" data-testid="wym-languages" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {d.languages.map(l => <Chip key={l.id} name={l.label} state={s.languages.includes(l.id) ? 'on' : 'off'} onClick={() => flipLang(l.id)} />)}
            </div>
          </div>
          <div>
            <h4 style={group}>Popularity</h4>
            <div className="gr-f3">
              <Field id="wym-tmdb-votes" label="Minimum TMDb votes"><NumField id="wym-tmdb-votes" value={s.min_tmdb_votes} grouped bad={!!bad.min_tmdb_votes} onCommit={v => put({ min_tmdb_votes: v })} /></Field>
              <Field id="wym-imdb-votes" label="Minimum IMDb votes"><NumField id="wym-imdb-votes" value={s.min_imdb_votes} grouped disabled={!d.imdb_active} bad={!!bad.min_imdb_votes} onCommit={v => put({ min_imdb_votes: v })} /></Field>
              <Field id="wym-trakt-watchers" label="Minimum Trakt watchers"><NumField id="wym-trakt-watchers" value={s.min_trakt_watchers} grouped bad={!!bad.min_trakt_watchers} onCommit={v => put({ min_trakt_watchers: v })} /></Field>
            </div>
          </div>
          <div>
            <h4 style={group}>Trakt lists</h4>
            <div className="wym-checks" role="group" aria-label="Trakt lists" data-testid="wym-trakt-lists">
              {d.trakt_lists.map(o => <Check key={o.id} id={`wym-trakt-${o.id}`} label={o.label} on={s.trakt_lists.includes(o.id)}
                onChange={v => put({ trakt_lists: toggleIn(s.trakt_lists, o.id, v, d.trakt_lists.map(x => x.id)) })} />)}
            </div>
          </div>
          <div>
            <h4 style={group}>TMDb lists</h4>
            <div className="wym-checks" role="group" aria-label="TMDb lists" data-testid="wym-tmdb-lists">
              {d.tmdb_lists.map(o => <Check key={o.id} id={`wym-tmdb-${o.id}`} label={o.label} on={s.tmdb_lists.includes(o.id)}
                onChange={v => put({ tmdb_lists: toggleIn(s.tmdb_lists, o.id, v, d.tmdb_lists.map(x => x.id)) })} />)}
            </div>
          </div>
          <div>
            <h4 style={group}>Genres</h4>
            <div role="group" aria-label="Genres" data-testid="wym-genres" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {d.genres.map(g => <Chip key={g} name={g} state={genreState(g)} onClick={() => flipGenre(g)} />)}
            </div>
          </div>
          <div>
            <h4 style={group}>Content</h4>
            <div className="gr-f2">
              <Switch id="wym-adult" label="Include adult" on={s.include_adult} onChange={v => put({ include_adult: v })} />
              <Switch id="wym-poster" label="Require poster" on={s.require_poster} onChange={v => put({ require_poster: v })} />
            </div>
          </div>
        </section>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          <StatusPanel status={d.status} />
          <Dismissed items={d.dismissed} onRestore={restore} busy={restoring} />
        </div>
      </div>
    </div>
  );
}
