/** v200 Settings > Movies (the native movie watchlist, piece 1): monitoring, quality profiles, watchlist picker, trackers,
 * preferred groups and naming (v220: Import card removed; its stored values are untouched), in TV Settings' visual style. Stored in movies.db; nothing reads Radarr. */
import { useEffect, useState, type CSSProperties } from 'react';
import { FONT } from '@/components/start/StartUI';
import { apiFetch } from '@/apiClient';
const W70 = 'rgba(255,255,255,0.7)';
import { Modal, Row, Select, Toggle, SAVE_ERROR, field, glassBtn, primaryBtn, tvEditCss } from '@/components/library/TvEdit';
import { PreferredGroups } from '@/components/library/TvLibrarySettings';
import { sectionLabel } from '@/components/library/LibraryPathsSettings';
import {
  deleteMovieProfile, loadMovieSettings, saveMovieProfile, saveMovieSettings,
  type MovieProfile, type MovieQuality, type MovieSettings,
} from './movieSettingsApi';

const W50 = 'rgba(255,255,255,0.5)';
const W95 = 'rgba(255,255,255,0.95)';
const ACCENT = '#3b82f6';
const card: CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' };
const rowStyle = (i: number): CSSProperties => ({ minHeight: 56, display: 'flex', alignItems: 'center', gap: 10, padding: '0 16px',
  borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none', fontFamily: FONT });
const small: CSSProperties = { ...glassBtn, height: 32, padding: '0 10px', fontSize: 13 };

/** v223 (Tester LOW): list fields that are not arrays in the API data read as empty, never crash the page. */
const arr = <T,>(x: T[] | null | undefined): T[] => (Array.isArray(x) ? x : []);
export function normMovieSettings(d: MovieSettings): MovieSettings {
  return { ...d, profiles: arr(d.profiles), qualities: arr(d.qualities), trackers: arr(d.trackers),
    picker_profiles: arr(d.picker_profiles), preferred_groups: arr(d.preferred_groups) };
}

/** v223 (Designer): at 600px and below Folder/File format stack the label above a full-width field. */
export const movieNamingCss = '@media (max-width: 600px){.mv-fmt{flex-direction:column!important;align-items:stretch!important;gap:6px!important;padding:10px 16px!important}'
  + '.mv-fmt>span{width:auto!important}.mv-fmt>input{width:100%!important;flex:none!important}}';

export function MoviesSettings({ wide = true }: { wide?: boolean } = {}) {
  const [d, setD] = useState<MovieSettings | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<MovieProfile | 'new' | null>(null);
  const load = () => void loadMovieSettings().then(r => { if (r.ok) { setD(normMovieSettings(r.data)); setFailed(false); } else setFailed(true); });
  useEffect(load, []);
  const put = (patch: Parameters<typeof saveMovieSettings>[0], optimistic: MovieSettings) => {
    const before = d;
    setD(optimistic); setError('');
    void saveMovieSettings(patch).then(r => { if (r.ok) setD(normMovieSettings(r.data)); else { setD(before); setError(SAVE_ERROR); } });
  };
  if (failed) return <div role="alert" style={{ ...card, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W50 }}>{"Couldn't load"}</div>;
  if (!d) return <div aria-busy="true" style={{ ...card, height: 120 }} />;
  const names = new Map(d.qualities.map(q => [q.id, q.name]));
  const trackers = d.trackers;
  const moveT = (i: number, dir: number) => {
    const j = i + dir;
    if (j < 0 || j >= trackers.length) return;
    const t = [...trackers];
    [t[i], t[j]] = [t[j]!, t[i]!];
    put({ trackers: t.map(x => ({ id: x.id, enabled: x.enabled })) }, { ...d, trackers: t });
  };
  return (
    <div data-testid="movies-settings" style={{ display: 'flex', flexDirection: 'column', gap: 32, fontFamily: FONT, padding: wide ? 0 : '8px 16px 0' }}>
      <style>{tvEditCss}</style>
      {error && <div role="alert" style={{ fontSize: 13, color: '#fca5a5' }}>{error}</div>}
      <div style={card}>
        <div style={rowStyle(0)}>
          <span id="mv-monitoring" style={{ fontSize: 14, fontWeight: 600, color: W95, flex: 1 }}>Monitoring</span>
          <Toggle id="mv-monitoring-toggle" label="Monitoring" on={d.monitoring} onChange={v => put({ monitoring: v }, { ...d, monitoring: v })} />
        </div>
      </div>

      <div data-testid="movie-profiles">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '0 4px 12px' }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: W95 }}>Quality profiles</h3>
          <button type="button" className="lp-focus tve-glass" data-testid="movie-profile-add" onClick={() => setEditing('new')}
            style={{ ...glassBtn, height: 36, padding: '0 14px' }}>Add profile</button>
        </div>
        <div style={card}>
          {d.profiles.map((p, i) => (
            <button key={p.id} type="button" className="lp-focus tve-row" data-testid="movie-profile-row" onClick={() => setEditing(p)}
              style={{ ...rowStyle(i), width: '100%', border: 'none', borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none', background: 'none', cursor: 'pointer', textAlign: 'left' }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: W95, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
              {p.default && <span data-testid="movie-profile-default" style={{ height: 22, padding: '0 9px', borderRadius: 999, display: 'inline-flex', alignItems: 'center',
                background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', fontSize: 12, fontWeight: 600, color: ACCENT }}>Default</span>}
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 13, color: W50 }}>{p.cutoff ? names.get(p.cutoff) || p.cutoff : ''}</span>
            </button>
          ))}
        </div>
      </div>

      <div data-testid="movie-picker-profiles">
        <h3 style={sectionLabel}>Watchlist picker</h3>
        <div style={card}>
          {d.profiles.map((p, i) => {
            const chosen = d.picker_profiles ?? [];
            const on = chosen.length === 0 || chosen.includes(p.id);
            return (
              <div key={p.id} data-testid="movie-picker-profile" style={rowStyle(i)}>
                <span style={{ flex: 1, fontSize: 14, color: on ? W95 : W50 }}>{p.name}</span>
                <Toggle id={`mv-picker-${p.id}`} label={p.name} on={on} onChange={v => {
                  const base = chosen.length ? chosen : d.profiles.map(x => x.id);
                  const next = v ? [...base, p.id] : base.filter(x => x !== p.id);
                  if (!next.length) return;                        // at least one profile stays in the picker
                  const all = next.length === d.profiles.length ? [] : d.profiles.map(x => x.id).filter(x => next.includes(x));
                  put({ picker_profiles: all }, { ...d, picker_profiles: all });
                }} />
              </div>
            );
          })}
        </div>
      </div>

      <div data-testid="movie-trackers">
        <h3 style={sectionLabel}>Trackers</h3>
        {trackers.length === 0 ? (
          <div style={{ ...card, height: 72, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, color: W50 }}>No trackers</div>
        ) : (
          <div style={card}>
            {trackers.map((t, i) => (
              <div key={t.id} data-testid="movie-tracker" style={rowStyle(i)}>
                <span style={{ minWidth: 20, fontSize: 13, color: W50 }}>{i + 1}</span>
                <span style={{ flex: 1, fontSize: 14, color: t.enabled ? W95 : W50 }}>{t.name}</span>
                <button type="button" className="lp-focus tve-glass" style={small} aria-label={`Move ${t.name} up`} disabled={i === 0} onClick={() => moveT(i, -1)}>↑</button>
                <button type="button" className="lp-focus tve-glass" style={small} aria-label={`Move ${t.name} down`} disabled={i === trackers.length - 1} onClick={() => moveT(i, 1)}>↓</button>
                <Toggle id={`mv-tracker-${t.id}`} label={t.name} on={t.enabled} onChange={v => {
                  const next = trackers.map(x => (x.id === t.id ? { ...x, enabled: v } : x));
                  put({ trackers: next.map(x => ({ id: x.id, enabled: x.enabled })) }, { ...d, trackers: next });
                }} />
              </div>
            ))}
          </div>
        )}
      </div>

      <PreferredGroups card value={d.preferred_groups ?? []} onChange={g => put({ preferred_groups: g }, { ...d, preferred_groups: g })} />

      <MovieNamingSettings />

      {editing && (
        <MovieProfileEditor profile={editing === 'new' ? null : editing} qualities={d.qualities}
          onClose={() => setEditing(null)} onSaved={load} />
      )}
    </div>
  );
}

const num = (v: string): number | null => (v.trim() === '' || Number.isNaN(Number(v)) ? null : Number(v));

export function MovieProfileEditor({ profile, qualities, onClose, onSaved }: {
  profile: MovieProfile | null; qualities: MovieQuality[]; onClose: () => void; onSaved: () => void;
}) {
  const [name, setName] = useState(profile?.name ?? '');
  const [items, setItems] = useState(profile?.items ?? qualities.map(q => ({ id: q.id, allowed: false })));
  const [cutoff, setCutoff] = useState<string | null>(profile?.cutoff ?? null);
  const [lo, setLo] = useState(profile?.min_mb_per_min == null ? '' : String(profile.min_mb_per_min));
  const [hi, setHi] = useState(profile?.max_mb_per_min == null ? '' : String(profile.max_mb_per_min));
  const [def, setDef] = useState(profile?.default ?? false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const names = new Map(qualities.map(q => [q.id, q.name]));
  const enabled = items.filter(i => i.allowed);
  const cut = cutoff && enabled.some(i => i.id === cutoff) ? cutoff : enabled[0]?.id ?? null;
  const sizesOk = (lo === '' || num(lo) !== null) && (hi === '' || num(hi) !== null) && !(num(lo) !== null && num(hi) !== null && num(lo)! > num(hi)!);
  const valid = name.trim().length > 0 && enabled.length > 0 && sizesOk;
  const move = (i: number, d: number) => setItems(x => {
    const j = i + d;
    if (j < 0 || j >= x.length) return x;
    const n = [...x];
    [n[i], n[j]] = [n[j]!, n[i]!];
    return n;
  });
  const save = async () => {
    if (!valid || busy) return;
    setBusy(true); setError('');
    const r = await saveMovieProfile({ name: name.trim(), items, cutoff: cut, min_mb_per_min: num(lo), max_mb_per_min: num(hi), default: def }, profile?.id);
    setBusy(false);
    if (!r.ok) { setError(SAVE_ERROR); return; }
    onSaved(); onClose();
  };
  const remove = async () => {
    if (!profile) return;
    setBusy(true);
    const r = await deleteMovieProfile(profile.id);
    setBusy(false);
    if (!r.ok) { setError(SAVE_ERROR); return; }
    onSaved(); onClose();
  };
  const footer = (
    <>
      {profile && !profile.default && <button type="button" className="lp-focus" data-testid="mvp-delete" onClick={() => void remove()}
        style={{ ...glassBtn, color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}>Delete</button>}
      {error && <span role="alert" style={{ fontSize: 13, color: '#fca5a5' }}>{error}</span>}
      <span style={{ flex: 1 }} />
      <button type="button" className="lp-focus tve-glass" onClick={onClose} style={glassBtn}>Cancel</button>
      <button type="button" className="lp-focus tve-primary" data-testid="mvp-save" disabled={!valid || busy} onClick={() => void save()} style={primaryBtn(valid && !busy)}>Save</button>
    </>
  );
  return (
    <Modal title={profile ? profile.name : 'New profile'} onClose={onClose} footer={footer} testid="movie-profile-editor">
      <Row label="Name" id="mvp-name">
        <input id="mvp-name" className="tve-in" data-testid="mvp-name" value={name} maxLength={60} onChange={e => setName(e.target.value)} style={field} />
      </Row>
      <Row label="Cutoff" id="mvp-cutoff">
        <Select id="mvp-cutoff" value={cut} options={enabled.map(i => ({ id: i.id, label: names.get(i.id) || i.id }))} onChange={v => setCutoff(v)} />
      </Row>
      <Row label="Min MB per minute" id="mvp-min">
        <input id="mvp-min" className="tve-in" data-testid="mvp-min" inputMode="decimal" value={lo} onChange={e => setLo(e.target.value)} style={field} />
      </Row>
      <Row label="Max MB per minute" id="mvp-max">
        <input id="mvp-max" className="tve-in" data-testid="mvp-max" inputMode="decimal" value={hi} onChange={e => setHi(e.target.value)} style={field} />
      </Row>
      <Row label="Default" id="mvp-default"><Toggle id="mvp-default" on={def} onChange={setDef} /></Row>
      <div className="tve-row" style={{ display: 'grid', gridTemplateColumns: '160px minmax(0, 1fr)', columnGap: 16 }}>
        <span id="mvp-qualities-label" style={{ fontSize: 14, fontWeight: 500, color: 'rgba(255,255,255,0.7)', textAlign: 'right', paddingTop: 10 }}>Qualities</span>
        <div role="list" aria-labelledby="mvp-qualities-label" data-testid="mvp-qualities"
          style={{ maxHeight: 320, overflowY: 'auto', borderRadius: 8, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)' }}>
          {items.map((it, i) => (
            <div key={it.id} role="listitem" data-testid="mvp-quality" data-id={it.id}
              style={{ height: 40, display: 'flex', alignItems: 'center', gap: 10, padding: '0 10px', borderTop: i ? '1px solid rgba(255,255,255,0.05)' : 'none' }}>
              <button type="button" role="checkbox" aria-checked={it.allowed} aria-label={names.get(it.id) || it.id} className="lp-focus"
                onClick={() => setItems(x => x.map(q => (q.id === it.id ? { ...q, allowed: !q.allowed } : q)))}
                style={{ width: 18, height: 18, padding: 0, borderRadius: 5, flex: 'none', cursor: 'pointer',
                  background: it.allowed ? ACCENT : 'none', border: it.allowed ? 'none' : '1.5px solid rgba(255,255,255,0.35)', color: '#fff', fontSize: 12 }}>
                {it.allowed ? '✓' : ''}
              </button>
              <span style={{ flex: 1, fontSize: 14, color: it.allowed ? W95 : W50 }}>{names.get(it.id) || it.id}</span>
              <button type="button" className="lp-focus tve-glass" style={{ ...small, height: 28 }} aria-label={`Move ${names.get(it.id) || it.id} up`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
              <button type="button" className="lp-focus tve-glass" style={{ ...small, height: 28 }} aria-label={`Move ${names.get(it.id) || it.id} down`} disabled={i === items.length - 1} onClick={() => move(i, 1)}>↓</button>
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}


// ------------------------------------------------------------------ v220 Naming (v203 Import card minus Library path / Rename movies)
// The library path and rename flag are left exactly as stored: the PUT carries only naming, with the stored rename echoed back
// because the backend stores the naming object whole.

interface ImportView { root: string; naming: { rename: boolean; folder: string; standard: string } }
const IMPORT_URL = '/api/settings/movies/import';

export function MovieNamingSettings() {
  const [v, setV] = useState<ImportView | null>(null);
  const [folder, setFolder] = useState('');
  const [file, setFile] = useState('');
  const [rename, setRename] = useState(true);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const take = (d: ImportView) => { setV(d); setFolder(d.naming.folder); setFile(d.naming.standard); setRename(d.naming.rename !== false); };
  useEffect(() => {
    void apiFetch(IMPORT_URL).then(r => r.ok ? r.json() : null).then(d => { if (d && d.naming) take(d as ImportView); }).catch(() => {});
  }, []);
  if (!v) return null;
  const dirty = folder !== v.naming.folder || file !== v.naming.standard || rename !== (v.naming.rename !== false);
  const save = async () => {
    setBusy(true); setErrors({}); setSaved(false);
    const body = { naming: { rename, folder, standard: file } };
    try {
      const r = await apiFetch(IMPORT_URL, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.naming) { take(d as ImportView); setSaved(true); } else setErrors(d.errors || { _: SAVE_ERROR });
    } catch { setErrors({ _: SAVE_ERROR }); }
    setBusy(false);
  };
  const err = (k: string) => errors[k] ? <div role="alert" style={{ fontSize: 12, color: '#fca5a5', marginTop: 4 }}>{errors[k]}</div> : null;
  return (
    <div data-testid="movie-naming-settings">
      <style>{movieNamingCss}</style>
      <h3 style={sectionLabel}>Naming</h3>
      <div style={card}>
        <div style={rowStyle(0)}>
          <span id="mv-rename" style={{ flex: 1, fontSize: 14, color: W95 }}>Rename movies</span>
          <Toggle id="mv-rename-toggle" label="Rename movies" on={rename} onChange={setRename} />
        </div>
        <label className="mv-fmt" style={{ ...rowStyle(1), fontSize: 14, color: W95 }}>
          <span style={{ width: 120, flex: 'none' }}>Folder format</span>
          <input id="mv-folder" className="tve-in" data-testid="mv-folder" value={folder} onChange={e => setFolder(e.target.value)} style={{ ...field, flex: 1, minWidth: 0 }} />
        </label>
        <label className="mv-fmt" style={{ ...rowStyle(2), fontSize: 14, color: rename ? W95 : W50 }}>
          <span style={{ width: 120, flex: 'none' }}>File format</span>
          <input id="mv-file" className="tve-in" data-testid="mv-file" value={file} disabled={!rename} onChange={e => setFile(e.target.value)} style={{ ...field, flex: 1, minWidth: 0 }} />
        </label>
        {err('naming') && <div style={{ padding: '0 16px 8px' }}>{err('naming')}</div>}
        <div style={{ ...rowStyle(3), justifyContent: 'flex-end', gap: 12 }}>
          {err('_')}
          {saved && !dirty && <span style={{ fontSize: 13, color: W70 }}>Saved</span>}
          <button type="button" className="lp-focus tve-primary" data-testid="mv-naming-save" disabled={!dirty || busy}
            onClick={() => void save()} style={primaryBtn(dirty && !busy)}>Save</button>
        </div>
      </div>
    </div>
  );
}
