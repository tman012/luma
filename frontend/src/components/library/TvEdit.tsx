/** v158a (docs/design/tv-edit.md): the shared 560px modal frame, field rows, the 40x22 toggle and the glass
 * select; the Edit series modal (from a Series poster); TV quality profiles (Options) and their editor.
 * Sentence case, no helper text, nothing pinned to the bottom of the screen. */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { FONT } from '@/components/start/StartUI';
import { FolderBrowser } from './FolderBrowser';
import {
  cutoffLabel, deleteProfile, deleteSeries, getSeries, loadProfiles, saveProfile, saveSeries,
  type Profile, type ProfileDraft, type Quality, type QualityItem, type SeriesEdit, type SeriesType, type SeriesView,
} from './tvCalendarApi';

const W30 = 'rgba(255,255,255,0.3)';
const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const ACCENT = '#3b82f6';
const RED = '#ef4444';
export const SAVE_ERROR = "Couldn't save";

/** v158b (Designer): the Edit and profile modals sit on Luma's surface, not a see-through tint of the poster. */
export const MODAL_SURFACE = 'rgba(21,16,42,0.92)';
export const tvEditCss = `
.tve-in:focus,.tve-in:focus-visible,.tve-sel:focus-visible{outline:none;border-color:${ACCENT}!important;box-shadow:0 0 0 3px rgba(59,130,246,.25)}
.tve-opt:hover{background:rgba(255,255,255,.06)}
.tve-primary:not(:disabled):hover{background:#60a5fa!important}
.tve-glass:hover{background:rgba(255,255,255,.10)!important}
@media (max-width:599px){.tve-row{grid-template-columns:minmax(0,1fr)!important;row-gap:6px}.tve-row>label,.tve-row>span{text-align:left!important;padding-top:0!important}}
.tve-row:hover{background:rgba(255,255,255,.04)}
.tve-cut{transition:height 150ms ease,opacity 150ms ease;overflow:hidden}
`;

// ---------------------------------------------------------------- frame + controls

export function Modal({ title, onClose, children, footer, testid, width = 560, actions }: {
  title: string; onClose: () => void; children: ReactNode; footer: ReactNode; testid: string;
  /** v181: the show sheet is wider; header actions sit left of the close button */
  width?: number; actions?: ReactNode;
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    document.addEventListener('keydown', k, true);
    return () => document.removeEventListener('keydown', k, true);
  }, [onClose]);
  return createPortal(
    <div data-testid={`${testid}-scrim`} onPointerDown={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(10,8,24,.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: FONT }}>
      <style>{tvEditCss}</style>
      <div role="dialog" aria-modal="true" aria-label={title} data-testid={testid}
        style={{ width, maxWidth: 'calc(100vw - 32px)', maxHeight: 'calc(100vh - 32px)', overflowY: 'auto', boxSizing: 'border-box', padding: 24,
          borderRadius: 16, background: MODAL_SURFACE, border: '1px solid rgba(255,255,255,0.08)',
          backdropFilter: 'blur(40px)', WebkitBackdropFilter: 'blur(40px)', boxShadow: '0 24px 64px rgba(0,0,0,.5)', color: W95 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, marginBottom: 20 }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, lineHeight: '24px', color: W95, minWidth: 0, overflowWrap: 'anywhere', flex: 1 }}>{title}</h2>
          {actions}
          <button type="button" aria-label="Close" className="lp-focus tve-glass" data-testid={`${testid}-close`} onClick={onClose} style={{ ...glassBtn, width: 36, height: 36, padding: 0, flex: 'none' }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>{children}</div>
        <div style={{ marginTop: 24, paddingTop: 16, borderTop: '1px solid rgba(255,255,255,0.08)', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>{footer}</div>
      </div>
    </div>,
    document.body,
  );
}

export function Row({ label, id, children }: { label: string; id: string; children: ReactNode }) {
  return (
    <div className="tve-row" style={{ display: 'grid', gridTemplateColumns: '160px minmax(0, 1fr)', columnGap: 16, alignItems: 'center' }}>
      <label htmlFor={id} id={`${id}-label`} style={{ fontSize: 14, fontWeight: 500, color: W70, textAlign: 'right' }}>{label}</label>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

export function Toggle({ id, on, onChange, label }: { id?: string; on: boolean; onChange: (on: boolean) => void; label?: string }) {
  return (
    <button type="button" role="switch" id={id} aria-checked={on} aria-label={label} aria-labelledby={label ? undefined : id && `${id}-label`}
      className="lp-focus" data-testid={id ? `${id}-toggle` : undefined} onClick={() => onChange(!on)}
      style={{ width: 40, height: 22, padding: 0, border: 'none', borderRadius: 11, position: 'relative', cursor: 'pointer', flex: 'none',
        background: on ? ACCENT : 'rgba(255,255,255,0.15)', transition: 'background-color 150ms', verticalAlign: 'middle' }}>
      <span aria-hidden="true" style={{ position: 'absolute', top: 2, left: on ? 20 : 2, width: 18, height: 18, borderRadius: 9, background: '#fff',
        transition: 'left 150ms', boxShadow: '0 1px 2px rgba(0,0,0,.3)' }} />
    </button>
  );
}

export function Select<V extends string | number>({ id, value, options, onChange, testid, up = false }: {
  id: string; value: V | null; options: { id: V; label: string }[]; onChange: (v: V) => void; testid?: string;
  /** v181: open upward (the bulk action bar at the bottom of the screen) */
  up?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const down = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); } };
    document.addEventListener('pointerdown', down, true);
    document.addEventListener('keydown', key, true);
    return () => { document.removeEventListener('pointerdown', down, true); document.removeEventListener('keydown', key, true); };
  }, [open]);
  const cur = options.find(o => o.id === value);
  return (
    <div ref={box} style={{ position: 'relative' }}>
      <button type="button" id={id} className="tve-sel" data-testid={testid || `${id}-select`} aria-haspopup="listbox" aria-expanded={open}
        aria-labelledby={`${id}-label ${id}`} onClick={() => setOpen(o => !o)}
        onKeyDown={e => {
          if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
          e.preventDefault();
          const i = options.findIndex(o => o.id === value);
          const n = options[Math.max(0, Math.min(options.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))];
          if (n) onChange(n.id);
        }}
        style={{ ...field, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, cursor: 'pointer', textAlign: 'left' }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{cur?.label ?? ''}</span>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={W50} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: 'none' }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div role="listbox" aria-labelledby={`${id}-label`} data-testid={`${id}-menu`}
          style={{ position: 'absolute', zIndex: 5, ...(up ? { bottom: 44 } : { top: 44 }), left: 0, right: 0, padding: 4, borderRadius: 8, maxHeight: 288, overflowY: 'auto',
            background: 'rgba(30,24,58,0.96)', border: '1px solid rgba(255,255,255,0.10)', backdropFilter: 'blur(24px)', WebkitBackdropFilter: 'blur(24px)',
            boxShadow: '0 16px 40px rgba(0,0,0,.45)' }}>
          {options.map(o => {
            const on = o.id === value;
            return (
              <button key={String(o.id)} type="button" role="option" aria-selected={on} className="tve-opt lp-focus"
                onClick={() => { onChange(o.id); setOpen(false); }}
                style={{ width: '100%', height: 36, padding: '0 10px', border: 'none', borderRadius: 6, background: 'none', display: 'flex',
                  alignItems: 'center', justifyContent: 'space-between', fontFamily: FONT, fontSize: 14, color: on ? ACCENT : W95, cursor: 'pointer', textAlign: 'left' }}>
                <span>{o.label}</span>
                {on && <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5" /></svg>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export const footBtn: React.CSSProperties = { height: 40, padding: '0 18px', borderRadius: 10, fontSize: 14, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', flex: 'none' };
export const glassBtn: React.CSSProperties = {
  ...footBtn, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(255,255,255,0.06)',
  border: '1px solid rgba(255,255,255,0.08)', color: W95,
};
export const primaryBtn = (enabled: boolean): React.CSSProperties => ({
  ...footBtn, border: 'none', background: ACCENT, color: '#fff', opacity: enabled ? 1 : 0.4, cursor: enabled ? 'pointer' : 'default',
});
export const field: React.CSSProperties = {
  width: '100%', height: 40, boxSizing: 'border-box', padding: '0 12px', borderRadius: 10, background: 'rgba(255,255,255,0.06)',
  border: '1px solid rgba(255,255,255,0.08)', color: W95, fontSize: 14, fontFamily: FONT,
};
const deleteBtn: React.CSSProperties = { ...glassBtn, color: RED, border: '1px solid rgba(239,68,68,0.3)' };
const ErrorText = ({ text }: { text: string }) => <span role="alert" data-testid="tve-error" style={{ fontSize: 13, color: RED }}>{text}</span>;

// ---------------------------------------------------------------- Edit series

export const NEW_SEASONS = [{ id: 'all' as const, label: 'All seasons' }, { id: 'none' as const, label: 'None' }];
export const SERIES_TYPES: { id: SeriesType; label: string }[] = [
  { id: 'standard', label: 'Standard' }, { id: 'daily', label: 'Daily' }, { id: 'anime', label: 'Anime' },
];
type Form = Required<Pick<SeriesEdit, 'monitored' | 'monitor_new_seasons' | 'monitor_specials' | 'season_folder' | 'series_type'>> & {
  profile_id: number | null; path: string;
};
const formOf = (v: SeriesView): Form => ({
  monitored: v.monitored, monitor_new_seasons: v.monitor_new_seasons, monitor_specials: v.monitor_specials, season_folder: v.season_folder,
  series_type: v.series_type, profile_id: v.profile_id, path: v.path || '',
});

export function EditSeriesModal({ seriesId, title, onClose, onSaved, onDeleted }: {
  seriesId: number; title: string; onClose: () => void; onSaved?: (v: SeriesView) => void; onDeleted?: () => void;
}) {
  const [view, setView] = useState<SeriesView | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [files, setFiles] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  useEffect(() => {
    let live = true;
    void getSeries(seriesId).then(r => { if (live && r.ok) { setView(r.data); setForm(formOf(r.data)); } else if (live) setError(SAVE_ERROR); });
    void loadProfiles().then(r => { if (live && r.ok) setProfiles(r.data.profiles); });
    return () => { live = false; };
  }, [seriesId]);
  const base = view ? formOf(view) : null;
  const changes: SeriesEdit = {};
  if (form && base) {
    (Object.keys(form) as (keyof Form)[]).forEach(k => { if (form[k] !== base[k]) (changes as Record<string, unknown>)[k] = form[k]; });
  }
  const dirty = Object.keys(changes).length > 0;
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm(f => (f ? { ...f, [k]: v } : f));
  const save = async () => {
    if (!dirty || busy) return;
    setBusy(true); setError('');
    const r = await saveSeries(seriesId, changes);
    setBusy(false);
    if (!r.ok) { setError(SAVE_ERROR); return; }
    setView(r.data); setForm(formOf(r.data)); onSaved?.(r.data); onClose();
  };
  const remove = async () => {
    setBusy(true); setError('');
    const r = await deleteSeries(seriesId, files);
    setBusy(false);
    if (!r.ok) { setError(SAVE_ERROR); return; }
    onDeleted?.(); onClose();
  };
  const footer = confirm ? (
    <>
      <span data-testid="tve-confirm" style={{ fontSize: 14, fontWeight: 500, color: W95, flex: '1 1 160px', minWidth: 160, overflowWrap: 'break-word' }}>{`Delete ${title}?`}</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 14, color: W70, whiteSpace: 'nowrap' }}>
        <Toggle id="tve-files" on={files} onChange={setFiles} label="Delete files too" />
        <span aria-hidden="true">Delete files too</span>
      </span>
      {error && <ErrorText text={error} />}
      <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 12 }}>
        <button type="button" className="lp-focus tve-glass" data-testid="tve-cancel-delete" onClick={() => setConfirm(false)} style={glassBtn}>Cancel</button>
        <button type="button" className="lp-focus" data-testid="tve-delete-confirm" disabled={busy} onClick={() => void remove()}
          style={{ ...footBtn, border: 'none', background: RED, color: '#fff' }}>Delete</button>
      </span>
    </>
  ) : (
    <>
      <button type="button" className="lp-focus" data-testid="tve-delete" onClick={() => setConfirm(true)} style={deleteBtn}>Delete</button>
      {error && <ErrorText text={error} />}
      <span style={{ flex: 1 }} />
      <button type="button" className="lp-focus tve-glass" data-testid="tve-cancel" onClick={onClose} style={glassBtn}>Cancel</button>
      <button type="button" className="lp-focus tve-primary" data-testid="tve-save" disabled={!dirty || busy} onClick={() => void save()} style={primaryBtn(dirty && !busy)}>Save</button>
    </>
  );
  return (
    <Modal title={`Edit ${title}`} onClose={onClose} footer={footer} testid="tv-edit-series">
      {!form ? (
        <div aria-busy="true" style={{ height: 6 * 40 + 5 * 16 }} />
      ) : (
        <>
          <Row label="Monitored" id="tve-monitored"><Toggle id="tve-monitored" on={form.monitored} onChange={v => set('monitored', v)} /></Row>
          <Row label="Monitor new seasons" id="tve-new-seasons">
            <Select id="tve-new-seasons" value={form.monitor_new_seasons} options={NEW_SEASONS} onChange={v => set('monitor_new_seasons', v)} />
          </Row>
          <Row label="Specials" id="tve-specials"><Toggle id="tve-specials" on={form.monitor_specials} onChange={v => set('monitor_specials', v)} /></Row>
          <Row label="Season folders" id="tve-season-folder"><Toggle id="tve-season-folder" on={form.season_folder} onChange={v => set('season_folder', v)} /></Row>
          <Row label="Quality profile" id="tve-profile">
            <Select id="tve-profile" value={form.profile_id} options={profiles.map(p => ({ id: p.id, label: p.name }))} onChange={v => set('profile_id', v)} />
          </Row>
          <Row label="Series type" id="tve-type">
            <Select id="tve-type" value={form.series_type} options={SERIES_TYPES} onChange={v => set('series_type', v)} />
          </Row>
          <Row label="Path" id="tve-path">
            <div style={{ display: 'flex' }}>
              <input id="tve-path" className="tve-in" data-testid="tve-path" value={form.path} onChange={e => set('path', e.target.value)} spellCheck={false}
                style={{ ...field, flex: 1, minWidth: 0, borderRadius: '10px 0 0 10px', borderRight: 'none' }} />
              <button type="button" aria-label="Browse" className="lp-focus tve-glass" data-testid="tve-browse" onClick={() => setBrowsing(b => !b)}
                style={{ ...glassBtn, width: 40, height: 40, padding: 0, borderRadius: '0 10px 10px 0' }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" />
                </svg>
              </button>
            </div>
            {browsing && (
              <div style={{ marginTop: 8 }}>
                <FolderBrowser start={form.path || null} onClose={() => setBrowsing(false)} onPick={p => { set('path', p); setBrowsing(false); }} />
              </div>
            )}
          </Row>
        </>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- quality profiles (Options)

export function QualityProfiles() {
  const [data, setData] = useState<{ profiles: Profile[]; qualities: Quality[] } | null>(null);
  const [editing, setEditing] = useState<Profile | 'new' | null>(null);
  const [failed, setFailed] = useState(false);
  const load = () => void loadProfiles().then(r => { if (r.ok) { setData(r.data); setFailed(false); } else setFailed(true); });
  useEffect(load, []);
  return (
    <div data-testid="tv-profiles">
      <style>{tvEditCss}</style>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '0 4px 12px' }}>
        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: W95 }}>Quality profiles</h3>
        <button type="button" className="lp-focus tve-glass" data-testid="tv-profile-add" onClick={() => setEditing('new')}
          style={{ ...glassBtn, height: 36, gap: 6, padding: '0 14px' }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
          Add profile
        </button>
      </div>
      {failed ? (
        <div role="alert" style={{ ...profilesCard, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W70 }}>{"Couldn't load"}</div>
      ) : !data ? (
        <div aria-busy="true" style={{ ...profilesCard, height: 120 }} />
      ) : !data.profiles.length ? (
        <div data-testid="tv-profiles-empty" style={{ ...profilesCard, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 500, color: W70 }}>
          No quality profiles
        </div>
      ) : (
        <div style={profilesCard}>
          {data.profiles.map((p, i) => (
            <button key={p.id} type="button" className="lp-focus tve-row" data-testid="tv-profile-row" onClick={() => setEditing(p)}
              style={{ width: '100%', height: 56, display: 'flex', alignItems: 'center', gap: 10, padding: '0 16px', border: 'none',
                borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none', background: 'none', cursor: 'pointer', fontFamily: FONT, textAlign: 'left' }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: W95, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
              {p.default && (
                <span data-testid="tv-profile-default" style={{ height: 22, padding: '0 9px', borderRadius: 999, display: 'inline-flex', alignItems: 'center',
                  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', fontSize: 12, fontWeight: 600, color: ACCENT }}>Default</span>
              )}
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 13, color: W50 }}>{cutoffLabel(p.cutoff)}</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={W50} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
            </button>
          ))}
        </div>
      )}
      {editing && data && (
        <ProfileEditor profile={editing === 'new' ? null : editing} qualities={data.qualities} onClose={() => setEditing(null)} onSaved={load} />
      )}
    </div>
  );
}
const profilesCard: React.CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' };

const draftOf = (p: Profile | null, qualities: Quality[]): ProfileDraft => {
  if (p) return { name: p.name, items: p.items, cutoff: p.cutoff, upgrades: p.upgrades, default: p.default };
  return { name: '', items: qualities.map(q => ({ id: q.id, allowed: false })), cutoff: null, upgrades: false, default: false };
};

export function ProfileEditor({ profile, qualities, onClose, onSaved }: {
  profile: Profile | null; qualities: Quality[]; onClose: () => void; onSaved: () => void;
}) {
  const [d, setD] = useState<ProfileDraft>(() => draftOf(profile, qualities));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState<number | null>(null);
  const names = new Map(qualities.map(q => [q.id, q.name]));
  const enabled = d.items.filter(i => i.allowed);
  const cutoff = d.cutoff && enabled.some(i => i.id === d.cutoff) ? d.cutoff : enabled[0]?.id ?? null;
  const dirty = JSON.stringify({ ...d, cutoff }) !== JSON.stringify(draftOf(profile, qualities)) || !profile;
  const valid = d.name.trim().length > 0 && enabled.length > 0;
  const cut = useRef<HTMLDivElement>(null);
  const [cutH, setCutH] = useState(0);
  useLayoutEffect(() => { setCutH(cut.current?.scrollHeight || 0); }, []);
  const move = (from: number, to: number) => setD(x => {
    if (from === to || to < 0 || to >= x.items.length) return x;
    const items = [...x.items];
    const [it] = items.splice(from, 1);
    items.splice(to, 0, it as QualityItem);
    return { ...x, items };
  });
  const save = async () => {
    if (!dirty || !valid || busy) return;
    setBusy(true); setError('');
    const r = await saveProfile({ ...d, name: d.name.trim(), cutoff }, profile?.id);
    setBusy(false);
    if (!r.ok) { setError(SAVE_ERROR); return; }
    onSaved(); onClose();
  };
  const remove = async () => {
    if (!profile) return;
    setBusy(true);
    const r = await deleteProfile(profile.id);
    setBusy(false);
    if (!r.ok) { setError(SAVE_ERROR); return; }
    onSaved(); onClose();
  };
  const canDelete = !!profile && !profile.default && !profile.in_use;
  const footer = (
    <>
      {canDelete && <button type="button" className="lp-focus" data-testid="tvp-delete" onClick={() => void remove()} style={deleteBtn}>Delete</button>}
      {error && <ErrorText text={error} />}
      <span style={{ flex: 1 }} />
      <button type="button" className="lp-focus tve-glass" data-testid="tvp-cancel" onClick={onClose} style={glassBtn}>Cancel</button>
      <button type="button" className="lp-focus tve-primary" data-testid="tvp-save" disabled={!dirty || !valid || busy} onClick={() => void save()}
        style={primaryBtn(dirty && valid && !busy)}>Save</button>
    </>
  );
  return (
    <Modal title={profile ? profile.name : 'New profile'} onClose={onClose} footer={footer} testid="tv-profile-editor">
      <Row label="Name" id="tvp-name">
        <input id="tvp-name" className="tve-in" data-testid="tvp-name" value={d.name} maxLength={60} onChange={e => setD({ ...d, name: e.target.value })} style={field} />
      </Row>
      <Row label="Upgrades" id="tvp-upgrades"><Toggle id="tvp-upgrades" on={d.upgrades} onChange={v => setD({ ...d, upgrades: v })} /></Row>
      <div ref={cut} className="tve-cut" data-testid="tvp-cutoff-row" aria-hidden={!d.upgrades}
        style={{ height: d.upgrades ? (cutH || 'auto') : 0, opacity: d.upgrades ? 1 : 0, margin: d.upgrades ? 0 : '-16px 0 0' }}>
        <Row label="Upgrade until" id="tvp-cutoff">
          <Select id="tvp-cutoff" value={cutoff} options={enabled.map(i => ({ id: i.id, label: names.get(i.id) || i.id }))} onChange={v => setD({ ...d, cutoff: v })} />
        </Row>
      </div>
      <Row label="Default" id="tvp-default"><Toggle id="tvp-default" on={d.default} onChange={v => setD({ ...d, default: v })} /></Row>
      <div className="tve-row" style={{ display: 'grid', gridTemplateColumns: '160px minmax(0, 1fr)', columnGap: 16 }}>
        <span id="tvp-qualities-label" style={{ fontSize: 14, fontWeight: 500, color: W70, textAlign: 'right', paddingTop: 10 }}>Qualities</span>
        <div role="list" aria-labelledby="tvp-qualities-label" data-testid="tvp-qualities"
          style={{ maxHeight: 320, overflowY: 'auto', borderRadius: 8, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)' }}>
          {d.items.map((it, i) => (
            <div key={it.id} role="listitem" data-testid="tvp-quality" data-id={it.id} draggable
              onDragStart={e => { setDrag(i); e.dataTransfer.effectAllowed = 'move'; }}
              onDragOver={e => { e.preventDefault(); if (drag !== null && drag !== i) { move(drag, i); setDrag(i); } }}
              onDragEnd={() => setDrag(null)}
              onKeyDown={e => {
                if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
                e.preventDefault(); move(i, i + (e.key === 'ArrowUp' ? -1 : 1));
              }}
              style={{ height: 40, display: 'flex', alignItems: 'center', gap: 10, padding: '0 10px', borderTop: i ? '1px solid rgba(255,255,255,0.05)' : 'none',
                background: drag === i ? 'rgba(255,255,255,0.12)' : undefined, boxShadow: drag === i ? '0 8px 24px rgba(0,0,0,.35)' : undefined, cursor: 'grab' }}>
              <svg width="10" height="16" viewBox="0 0 10 16" fill={W30} aria-hidden="true">
                {[2, 8, 14].map(y => <g key={y}><circle cx="2" cy={y} r="1.5" /><circle cx="8" cy={y} r="1.5" /></g>)}
              </svg>
              <button type="button" role="checkbox" aria-checked={it.allowed} aria-label={names.get(it.id) || it.id} className="lp-focus"
                data-testid="tvp-quality-check"
                onClick={() => setD(x => ({ ...x, items: x.items.map(q => (q.id === it.id ? { ...q, allowed: !q.allowed } : q)) }))}
                style={{ width: 18, height: 18, padding: 0, borderRadius: 5, flex: 'none', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  background: it.allowed ? ACCENT : 'none', border: it.allowed ? 'none' : '1.5px solid rgba(255,255,255,0.35)' }}>
                {it.allowed && <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5" /></svg>}
              </button>
              <span style={{ fontSize: 14, color: it.allowed ? W95 : W50 }}>{names.get(it.id) || it.id}</span>
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}
