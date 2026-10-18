/** Settings > Library paths (owner only, luma-test builds): where finished
 * movie downloads are copied. Paths, ordered rules (first match wins, drag or
 * arrow keys to reorder, switch on/off, instant save), the fallback, Copy or
 * Hardlink, and an optional qBittorrent-to-Luma path map. */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { T } from '@/theme/tokens';
import { FONT } from '@/components/start/StartUI';
import { NoticeToast } from '@/components/start/SetupToast';
import { CheckIcon, ChevronIcon, GripIcon, XIcon } from '@/components/nav/AppNav';
import { move } from '@/components/home/homeApi';
import { CategoryChip, PlusIcon, Switch, trackersCss } from '@/components/trackers/trackersUi';
import { FolderBrowser } from './FolderBrowser';
import { isMinimal } from '@/minimal';
import { loadTvJobs, retryTvJob, tvJobStatus, type TvJob } from './tvCalendarApi';
import {
  RESOLUTIONS, SAVE_FAILED, SOURCES, addPath, deletePath, editPath, loadLibrary, moviePaths, retryJob, ruleSummary, saveMapping, saveRules, sinceText,
  type Errors, type LibPath, type LibraryView, type PathMapRow, type Result, type Rule, type Transfer, type Unit,
} from './libraryPathsApi';

const ROW_H = 64;
/** Desktop (>=1024px) or phone, for type sizes and chip heights. */
export const WideCtx = createContext(true);
type Editing = { kind: 'path'; id: string | null } | { kind: 'rule'; index: number | null } | { kind: 'map' } | null;

export function LibraryPathsSettings({ wide }: { wide: boolean }) {
  const [view, setView] = useState<LibraryView | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [toast, setToast] = useState('');
  const [editing, setEditing] = useState<Editing>(null);
  const [fbError, setFbError] = useState('');
  const seq = useRef(0);

  useEffect(() => {
    loadLibrary().then(r => (r.ok ? setView(r.view) : setLoadFailed(true)));
  }, []);

  // Movies choices leave out paths TV selected (as the server's pick_destination does).
  const fallbackFor = (v: LibraryView) => {
    const movies = moviePaths(v.paths);
    if (v.fallback_path_id && movies.some(p => p.id === v.fallback_path_id)) return v.fallback_path_id;
    return movies.length === 1 ? movies[0]?.id ?? null : null;
  };

  /** Optimistic save of the rules list (switch, reorder); reverts on failure. */
  const commitRules = useCallback((v: LibraryView, rules: Rule[]) => {
    const before = v;
    const fb = fallbackFor(v);
    setView({ ...v, rules, fallback_path_id: fb });
    const n = ++seq.current;
    saveRules(rules, fb).then(r => {
      if (n !== seq.current) return;
      if (r.ok) { setView(r.view); setFbError(''); return; }
      setView(before);
      if (r.errors.fallback_path_id) setFbError(r.errors.fallback_path_id);
      setToast(r.error || SAVE_FAILED);
    });
  }, []);

  if (!view) {
    return loadFailed
      ? <div role="alert" style={{ ...pad(wide), ...muted }}>{"Couldn't load library paths"}</div>
      : null;
  }
  const done = (r: Result, msg = 'Saved') => {
    if (r.ok) { setView(r.view); setEditing(null); setToast(msg); }
    return r;
  };
  const byId = new Map(view.paths.map(p => [p.id, p]));
  const fb = fallbackFor(view);
  const movies = moviePaths(view.paths);

  return (
    <WideCtx.Provider value={wide}>
    <section data-testid="library-paths-section" style={{ ...pad(wide), display: 'flex', flexDirection: 'column', gap: 28, fontFamily: FONT }}>
      <style>{libraryPathsCss}</style>
      <style>{trackersCss}</style>
      {toast && <NoticeToast key={toast + seq.current} text={toast} onDone={() => setToast('')} />}

      <DownloadsStatus view={view} onView={setView} />

      <div data-testid="lp-paths">
        <h3 style={sectionLabel}>Paths</h3>
        <div style={card}>
          {view.paths.map((p, i) => (editing?.kind === 'path' && editing.id === p.id
            ? <PathForm key={p.id} path={p} first={i === 0} onCancel={() => setEditing(null)}
                onSave={(name, path) => editPath(p.id, name, path).then(r => done(r))}
                onDelete={() => deletePath(p.id).then(r => {
                  if (!r.ok && !Object.keys(r.errors).length) setToast(r.error);
                  return done(r, 'Removed');
                })} />
            : <PathRow key={p.id} path={p} first={i === 0} onOpen={() => setEditing({ kind: 'path', id: p.id })} />))}
          {editing?.kind === 'path' && editing.id === null
            ? <PathForm first={view.paths.length === 0} onCancel={() => setEditing(null)}
                onSave={(name, path) => addPath(name, path).then(r => done(r, 'Added'))} />
            : <AddRow label="Add path" first={view.paths.length === 0} testId="lp-add-path"
                onClick={() => setEditing({ kind: 'path', id: null })} />}
        </div>
      </div>

      {movies.length > 0 && (
        <div data-testid="lp-rules">
          <h3 style={sectionLabel}>Rules</h3>
          <RulesList view={view} paths={movies} byId={byId} editing={editing} setEditing={setEditing} commit={commitRules}
            onSaveRule={(index, rule) => {
              const rules = index === null ? [...view.rules, rule] : view.rules.map((x, i) => (i === index ? rule : x));
              return saveRules(rules, fb).then(r => {
                if (!r.ok && r.errors.fallback_path_id) setFbError(r.errors.fallback_path_id);
                return done(r, index === null ? 'Added' : 'Saved');
              });
            }}
            onDeleteRule={index => saveRules(view.rules.filter((_, i) => i !== index), fb).then(r => done(r, 'Removed'))} />
          {movies.length > 1 && (
            <div data-testid="lp-fallback" style={{ marginTop: 12 }}>
              <Picker label="Fallback" options={movies.map(p => ({ id: p.id, label: p.name }))} value={fb} error={fbError}
                onChoose={id => {
                  setFbError('');
                  saveRules(view.rules, id).then(r => {
                    if (r.ok) { setView(r.view); return; }
                    if (r.errors.fallback_path_id) setFbError(r.errors.fallback_path_id);   // e.g. "This path is used by TV"
                    else setToast(r.error);
                  });
                }} />
            </div>
          )}
        </div>
      )}

      <div data-testid="lp-transfer">
        <h3 style={sectionLabel}>Transfer</h3>
        <Segmented value={view.transfer} onChange={t => {
          const before = view;
          setView({ ...view, transfer: t });
          saveMapping({ transfer: t }).then(r => { if (r.ok) setView(r.view); else { setView(before); setToast(r.error); } });
        }} />
      </div>

      <div data-testid="lp-path-map">
        <h3 style={sectionLabel}>Path map</h3>
        <div style={card}>
          {view.path_map.map((m, i) => (
            <MapRow key={`${m.remote}\u0000${m.local}`} row={m} first={i === 0}
              onRemove={() => saveMapping({ path_map: view.path_map.filter((_, j) => j !== i) }).then(r => {
                if (!r.ok) setToast(r.error); else done(r, 'Removed');
              })} />
          ))}
          {editing?.kind === 'map'
            ? <MapForm first={view.path_map.length === 0} index={view.path_map.length} onCancel={() => setEditing(null)}
                onSave={row => saveMapping({ path_map: [...view.path_map, row] }).then(r => done(r, 'Added'))} />
            : <AddRow label="Add mapping" first={view.path_map.length === 0} testId="lp-add-map"
                onClick={() => setEditing({ kind: 'map' })} />}
        </div>
      </div>
    </section>
    </WideCtx.Provider>
  );
}

// ---------------------------------------------------------------- downloads

/** The downloads folder the mover reads, and grabs whose files it can't find. */
function DownloadsStatus({ view, onView }: { view: LibraryView; onView?: (v: LibraryView) => void }) {
  const wide = useContext(WideCtx);
  const dl = view.downloads;
  const lost = view.not_found || [];
  const failed = view.failed || [];
  const [busy, setBusy] = useState<string | null>(null);
  // v158c: the TV download jobs that failed, each with its error; no-space ones get Retry
  // (the existing TV retry endpoint). No TV (404) or unreachable: no rows.
  const [tvJobs, setTvJobs] = useState<TvJob[]>([]);
  const loadTv = useCallback(() => {
    if (!isMinimal()) return undefined;                 // TV lives in the minimal app only
    let live = true;
    void loadTvJobs().then(r => { if (live) setTvJobs(r.ok ? r.data.failed : []); });
    return () => { live = false; };
  }, []);
  useEffect(loadTv, [loadTv]);
  if (!dl && !lost.length && !failed.length && !tvJobs.length) return null;
  const s = STATUS[dl?.status === 'missing' ? 'missing' : 'ok'];
  return (
    <div data-testid="lp-downloads">
      <h3 style={sectionLabel}>Downloads</h3>
      <div style={card}>
        {dl && (
          <div data-testid="lp-downloads-folder" data-status={dl.status} style={{ ...rowBtn, cursor: 'default' }}>
            <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 4, background: s.color, flex: 'none' }} />
            <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={titleText(wide)}>Downloads folder</span>
              <span style={subText(wide)} title={dl.path}>{dl.path}</span>
            </span>
            {s.text && (
              <span data-testid="lp-downloads-status" style={{ fontSize: 13, fontWeight: 500, color: s.color, flex: 'none' }}>{s.text}</span>
            )}
          </div>
        )}
        {lost.map((j, i) => (
          <div key={`${j.title}\u0000${j.since}`} data-testid="lp-not-found" style={{ ...rowBtn, cursor: 'default', ...(dl || i > 0 ? topBorder : null) }}>
            <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 4, background: T.danger, flex: 'none' }} />
            <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={titleText(wide)}>{j.title || 'Untitled'}</span>
              <span style={subText(wide)}>{sinceText(j.since)}</span>
            </span>
            <span data-testid="lp-not-found-status" style={{ fontSize: 13, fontWeight: 500, color: T.danger, flex: 'none' }}>
              {j.reason === 'not_mapped' ? 'Path not mapped' : 'Files not found'}
            </span>
          </div>
        ))}
        {failed.map((j, i) => (
          <JobRow key={j.hash} testId="lp-failed" title={j.title || 'Untitled'} since={j.since} status="Not enough space"
            top={!!(dl || lost.length || i > 0)} busy={busy === j.hash}
            onRetry={() => { setBusy(j.hash); void retryJob(j.hash).then(r => { setBusy(null); if (r.ok) onView?.(r.view); }); }} />
        ))}
        {tvJobs.map((j, i) => (
          <JobRow key={`tv:${j.hash}`} testId="lp-tv-job" state={j.state} title={j.name || 'Untitled'} since={j.at} status={tvJobStatus(j)}
            top={!!(dl || lost.length || failed.length || i > 0)} busy={busy === `tv:${j.hash}`}
            onRetry={j.retry ? () => { setBusy(`tv:${j.hash}`); void retryTvJob(j.hash).then(r => { setBusy(null); if (r.ok) loadTv(); }); } : undefined} />
        ))}
      </div>
    </div>
  );
}

/** v165 (Designer): a phone (under 600px) gives the job's name the whole first line (ellipsis) and puts
 * the status and its real failure date on the second, in red: 'Not enough space · Since Oct 15'. */
export const PHONE_Q = '(max-width: 599px)';
export function usePhone(): boolean {
  const get = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(PHONE_Q).matches;
  const [phone, setPhone] = useState(get);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const m = window.matchMedia(PHONE_Q);
    const on = () => setPhone(m.matches);
    on();
    m.addEventListener?.('change', on);
    return () => m.removeEventListener?.('change', on);
  }, []);
  return phone;
}

/** One failed download job (Movies mover or TV): name, failure date, status, Retry when offered. */
function JobRow({ testId, state, title, since, status, top, busy, onRetry }: {
  testId: string; state?: string; title: string; since: number | null | undefined; status: string; top: boolean; busy: boolean; onRetry?: () => void;
}) {
  const wide = useContext(WideCtx);
  const phone = usePhone();
  const retry = onRetry && (
    <button type="button" data-testid={`${testId}-retry`} className="lp-focus" disabled={busy} aria-label={`Retry ${title}`} onClick={onRetry}
      style={{ flex: 'none', padding: '6px 12px', borderRadius: 999, border: '1px solid rgba(255,255,255,0.16)', background: 'rgba(255,255,255,0.06)',
        color: 'rgba(255,255,255,0.95)', fontSize: 13, fontWeight: 600, cursor: 'pointer', opacity: busy ? 0.4 : 1 }}>Retry</button>
  );
  const dot = <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 4, background: T.danger, flex: 'none' }} />;
  if (phone) {
    const line2 = since ? `${status} \u00b7 ${sinceText(since)}` : status;
    return (
      <div data-testid={testId} data-state={state} data-layout="phone" style={{ ...rowBtn, cursor: 'default', ...(top ? topBorder : null) }}>
        {dot}
        <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span data-testid={`${testId}-name`} title={title} style={{ ...titleText(wide), display: 'block' }}>{title}</span>
          <span data-testid={`${testId}-status`} title={line2}
            style={{ fontSize: 13, fontWeight: 500, color: T.danger, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
            {status}{since ? <>{' \u00b7 '}<span data-testid={`${testId}-since`} style={{ whiteSpace: 'nowrap' }}>{sinceText(since)}</span></> : null}
          </span>
        </span>
        {retry}
      </div>
    );
  }
  return (
    <div data-testid={testId} data-state={state} style={{ ...rowBtn, cursor: 'default', ...(top ? topBorder : null) }}>
      {dot}
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span data-testid={`${testId}-name`} style={titleText(wide)} title={title}>{title}</span>
        {since ? <span style={subText(wide)}>{sinceText(since)}</span> : null}
      </span>
      <span data-testid={`${testId}-status`} title={status}
        style={{ fontSize: 13, fontWeight: 500, color: T.danger, flex: '0 1 auto', minWidth: 0, maxWidth: wide ? 320 : 160, textAlign: 'right',
          overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: wide ? 2 : 3, WebkitBoxOrient: 'vertical' }}>{status}</span>
      {retry}
    </div>
  );
}

// ---------------------------------------------------------------- paths

const STATUS: Record<LibPath['status'], { color: string; text: string }> = {
  ok: { color: T.success, text: '' },
  missing: { color: T.danger, text: 'Missing' },
  read_only: { color: T.warning, text: 'Read-only' },
};

export function PathRow({ path, first, onOpen }: { path: LibPath; first: boolean; onOpen: () => void }) {
  const s = STATUS[path.status] || STATUS.missing;
  const wide = useContext(WideCtx);
  return (
    <button type="button" className="lp-row" data-testid="lp-path" data-status={path.status} onClick={onOpen}
      aria-label={`Edit ${path.name}`} style={{ ...rowBtn, ...(first ? null : topBorder) }}>
      <span aria-hidden="true" data-testid="lp-status-dot" style={{ width: 8, height: 8, borderRadius: 4, background: s.color, flex: 'none' }} />
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2, textAlign: 'left' }}>
        <span style={titleText(wide)}>{path.name}</span>
        <span style={subText(wide)} title={path.path}>{path.path}</span>
      </span>
      {s.text && (
        <span data-testid="lp-status" style={{ fontSize: 13, fontWeight: 500, color: s.color, flex: 'none', textAlign: 'right' }}>{s.text}</span>
      )}
      <span style={{ display: 'inline-flex', color: T.text4, flex: 'none' }}><ChevronIcon /></span>
    </button>
  );
}

export function PathForm({ path, first, onSave, onCancel, onDelete }: {
  path?: LibPath; first: boolean; onCancel: () => void;
  onSave: (name: string, path: string) => Promise<{ ok: boolean; errors?: Errors }>;
  onDelete?: () => Promise<{ ok: boolean; errors?: Errors }>;
}) {
  const [name, setName] = useState(path?.name || '');
  const [dir, setDir] = useState(path?.path || '');
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const run = async (fn: () => Promise<{ ok: boolean; errors?: Errors }>) => {
    setBusy(true);
    const r = await fn();
    setBusy(false);
    if (!r.ok) setErrors(r.errors || {});
  };
  const dirty = name.trim() !== (path?.name || '') || dir.trim() !== (path?.path || '');
  return (
    <form data-testid="lp-path-form" style={{ ...formBox, ...(first ? null : topBorder) }}
      onSubmit={e => { e.preventDefault(); if (dirty && !busy) run(() => onSave(name.trim(), dir.trim())); }}>
      <Field id="lp-name" label="Name" value={name} error={errors.name} onChange={setName} autoFocus maxLength={40} />
      <div>
        <Field id="lp-dir" label="Folder" value={dir} error={errors.path} onChange={setDir} mono />
        {browsing
          ? <div style={{ marginTop: 10 }}>
              <FolderBrowser start={dir.trim() || null} onClose={() => setBrowsing(false)}
                onPick={p => { setDir(p); setBrowsing(false); }} />
            </div>
          : <button type="button" className="lp-focus" data-testid="lp-browse" onClick={() => setBrowsing(true)}
              style={{ ...ghostBtn, marginTop: 10 }}>Browse</button>}
      </div>
      <FormButtons busy={busy} canSave={dirty && !!name.trim() && !!dir.trim()} onCancel={onCancel}
        onDelete={onDelete && (() => run(onDelete))} />
    </form>
  );
}

// ---------------------------------------------------------------- rules

function RulesList({ view, paths, byId, editing, setEditing, commit, onSaveRule, onDeleteRule }: {
  /** The Movies destinations a rule may pick (not TV-selected). */
  view: LibraryView; paths: LibPath[]; byId: Map<string, LibPath>; editing: Editing; setEditing: (e: Editing) => void;
  commit: (v: LibraryView, rules: Rule[]) => void;
  onSaveRule: (index: number | null, rule: Rule) => Promise<Result>; onDeleteRule: (index: number) => Promise<Result>;
}) {
  const rules = view.rules;
  const wide = useContext(WideCtx);
  const [drag, setDrag] = useState<{ key: string; from: number; startY: number; dy: number } | null>(null);
  const handles = useRef(new Map<string, HTMLButtonElement>());
  const refocus = useRef<string | null>(null);
  useEffect(() => {
    if (refocus.current) { handles.current.get(refocus.current)?.focus(); refocus.current = null; }
  });
  const keyOf = (r: Rule, i: number) => r.id || `new-${i}`;
  const target = (d: NonNullable<typeof drag>) => Math.max(0, Math.min(rules.length - 1, d.from + Math.round(d.dy / ROW_H)));
  const shiftFor = (i: number) => {
    if (!drag) return 0;
    const t = target(drag);
    if (drag.from < t && i > drag.from && i <= t) return -ROW_H;
    if (drag.from > t && i >= t && i < drag.from) return ROW_H;
    return 0;
  };
  const adding = editing?.kind === 'rule' && editing.index === null;
  return (
    <div style={card}>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, position: 'relative' }} data-testid="lp-rule-list">
        {rules.map((r, i) => {
          const key = keyOf(r, i);
          if (editing?.kind === 'rule' && editing.index === i) {
            return (
              <li key={key}>
                <RuleForm index={i} rule={r} paths={paths} first={i === 0} onCancel={() => setEditing(null)}
                  onSave={rule => onSaveRule(i, rule)} onDelete={() => onDeleteRule(i)} />
              </li>
            );
          }
          const lifted = drag?.key === key;
          const y = lifted ? drag!.dy : shiftFor(i);
          const dest = byId.get(r.path_id)?.name || '';
          return (
            <li key={key} data-testid="lp-rule" data-lifted={lifted ? 'true' : undefined}
              style={{
                ...ruleRow, ...(i === 0 ? null : topBorder),
                transform: `translateY(${y}px)${lifted ? ' scale(1.02)' : ''}`,
                transition: lifted ? 'none' : 'transform 150ms', ...(lifted ? liftedRow : null),
              }}>
              <button type="button" aria-label={`Reorder rule ${i + 1}`} style={handle} className="lp-focus"
                ref={el => { if (el) handles.current.set(key, el); else handles.current.delete(key); }}
                onPointerDown={e => {
                  e.currentTarget.setPointerCapture?.(e.pointerId);
                  setDrag({ key, from: i, startY: e.clientY, dy: 0 });
                  try { navigator.vibrate?.(10); } catch { /* unsupported */ }
                }}
                onPointerMove={e => { if (drag?.key === key) setDrag({ ...drag, dy: e.clientY - drag.startY }); }}
                onPointerUp={() => {
                  if (drag?.key !== key) return;
                  const t = target(drag);
                  setDrag(null);
                  if (t !== drag.from) commit(view, move(rules, drag.from, t));
                }}
                onPointerCancel={() => setDrag(null)}
                onKeyDown={e => {
                  const to = e.key === 'ArrowUp' ? i - 1 : e.key === 'ArrowDown' ? i + 1 : null;
                  if (to === null) return;
                  e.preventDefault();
                  if (to < 0 || to >= rules.length) return;
                  refocus.current = key;
                  commit(view, move(rules, i, to));
                }}>
                <GripIcon />
              </button>
              <button type="button" className="lp-row" aria-label={`Edit rule ${i + 1}`} onClick={() => setEditing({ kind: 'rule', index: i })}
                style={{ ...rowBtnBare, opacity: r.enabled ? 1 : 0.5 }}>
                <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2, textAlign: 'left' }}>
                  <span style={titleText(wide)} data-testid="lp-rule-summary">{ruleSummary(r)}</span>
                  <span style={subText(wide)} data-testid="lp-rule-dest">{dest}</span>
                </span>
              </button>
              <Switch on={r.enabled} label={`Rule ${i + 1}`}
                onChange={on => commit(view, rules.map((x, j) => (j === i ? { ...x, enabled: on } : x)))} />
            </li>
          );
        })}
      </ul>
      {adding
        ? <RuleForm index={rules.length} paths={paths} first={rules.length === 0} onCancel={() => setEditing(null)}
            onSave={rule => onSaveRule(null, rule)} />
        : <AddRow label="Add rule" first={rules.length === 0} testId="lp-add-rule" onClick={() => setEditing({ kind: 'rule', index: null })} />}
    </div>
  );
}

function RuleForm({ index, rule, paths, first, onSave, onCancel, onDelete }: {
  index: number; rule?: Rule; paths: LibPath[]; first: boolean; onCancel: () => void;
  onSave: (r: Rule) => Promise<Result>; onDelete?: () => Promise<Result>;
}) {
  const [pathId, setPathId] = useState(rule?.path_id || '');
  const [unit, setUnit] = useState<Unit | ''>(rule?.released_within?.unit || '');
  const [n, setN] = useState(String(rule?.released_within?.n || 1));
  const [res, setRes] = useState<string[]>(rule?.resolution || []);
  const [src, setSrc] = useState<string[]>(rule?.source || []);
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);
  const k = `rules.${index}`;
  const num = Number(n);
  const nOk = /^\d{1,3}$/.test(n) && num >= 1;
  const run = async (fn: () => Promise<Result>) => {
    setBusy(true);
    const r = await fn();
    setBusy(false);
    if (!r.ok) setErrors(r.errors);
  };
  const toggle = (list: string[], set: (v: string[]) => void, id: string) =>
    set(list.includes(id) ? list.filter(x => x !== id) : [...list, id]);
  const submit = () => run(() => onSave({
    ...(rule?.id ? { id: rule.id } : {}), enabled: rule?.enabled ?? true, path_id: pathId,
    released_within: unit ? { n: num, unit } : null, resolution: res, source: src,
  }));
  return (
    <form data-testid="lp-rule-form" style={{ ...formBox, ...(first ? null : topBorder) }}
      onSubmit={e => { e.preventDefault(); if (!busy && pathId && (!unit || nOk)) submit(); }}>
      <div>
        <span id="lp-dest-label" style={labelStyle}>Destination</span>
        <RowSelect labelId="lp-dest-label" testId="lp-dest" value={pathId} error={errors[`${k}.path_id`]}
          options={paths.map(p => ({ id: p.id, label: p.name }))} onChoose={setPathId} />
        {errors[`${k}.path_id`] && <span style={fieldError}>{errors[`${k}.path_id`]}</span>}
      </div>
      <div>
        <span id="lp-released-label" style={labelStyle}>Released within</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
          {unit && (
            <input id="lp-released-n" data-testid="lp-released-n" aria-label="Number" inputMode="numeric" value={n}
              onChange={e => setN(e.target.value.replace(/\D/g, '').slice(0, 3))}
              style={{ ...inputStyle, width: 80, flex: 'none', ...(!nOk || errors[`${k}.released_within`] ? invalid : null) }} />
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <RowSelect labelId="lp-released-label" testId="lp-released" value={unit}
              options={[{ id: '', label: 'Any time' }, { id: 'months', label: 'Months' }, { id: 'years', label: 'Years' }]}
              onChoose={v => setUnit(v as Unit | '')} />
          </div>
        </div>
        {errors[`${k}.released_within`] && <span style={fieldError}>{errors[`${k}.released_within`]}</span>}
      </div>
      <Chips label="Resolution" options={RESOLUTIONS} value={res} onToggle={id => toggle(res, setRes, id)} />
      <Chips label="Source" options={SOURCES} value={src} onToggle={id => toggle(src, setSrc, id)} />
      <FormButtons busy={busy} canSave={!!pathId && (!unit || nOk)} onCancel={onCancel} onDelete={onDelete && (() => run(onDelete))} />
    </form>
  );
}

function Chips({ label, options, value, onToggle }: {
  label: string; options: { id: string; label: string }[]; value: string[]; onToggle: (id: string) => void;
}) {
  const wide = useContext(WideCtx);
  return (
    <div role="group" aria-label={label}>
      <span style={labelStyle}>{label}</span>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {options.map(o => (
          <CategoryChip key={o.id} on={value.includes(o.id)} label={o.label} wide={wide}
            testId={`lp-chip-${o.id}`} onToggle={() => onToggle(o.id)} />
        ))}
      </div>
    </div>
  );
}

/** An input-style row (44px) that opens its choices below, like the Fallback
 * picker; no native select arrow. */
function RowSelect({ labelId, testId, value, options, error, onChoose }: {
  labelId: string; testId: string; value: string; options: { id: string; label: string }[];
  error?: string; onChoose: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const current = options.find(o => o.id === value);
  return (
    <div style={{ ...selectBox, ...(error ? invalid : null) }}>
      <button type="button" className="lp-row" data-testid={testId} aria-expanded={open} aria-labelledby={`${labelId} ${testId}-value`}
        onClick={() => setOpen(o => !o)} style={selectRow}>
        <span id={`${testId}-value`} style={{ flex: 1, minWidth: 0, textAlign: 'left', whiteSpace: 'nowrap', overflow: 'hidden',
          textOverflow: 'ellipsis', color: current ? T.white : T.text3 }}>
          {current ? current.label : 'Choose'}
        </span>
        <span aria-hidden="true" style={{ display: 'inline-flex', color: T.text3, transition: 'transform 150ms', transform: open ? 'rotate(90deg)' : 'none' }}>
          <ChevronIcon />
        </span>
      </button>
      {open && (
        <div role="radiogroup" aria-labelledby={labelId}>
          {options.map(o => {
            const on = o.id === value;
            return (
              <button key={o.id || 'none'} type="button" role="radio" aria-checked={on} className="lp-row"
                onClick={() => { setOpen(false); onChoose(o.id); }} style={{ ...selectRow, ...topBorder }}>
                <span style={{ flex: 1, textAlign: 'left' }}>{o.label}</span>
                <span style={{ display: 'inline-flex', color: T.accent, width: 20 }}>{on && <CheckIcon />}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- fallback, transfer, path map

function Picker({ label, options, value, error, onChoose }: {
  label: string; options: { id: string; label: string }[]; value: string | null; error?: string; onChoose: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const current = options.find(o => o.id === value);
  const wide = useContext(WideCtx);
  return (
    <>
      <div style={{ ...card, ...(error ? invalid : null) }}>
        <button type="button" className="lp-row" style={rowBtn} aria-expanded={open} onClick={() => setOpen(o => !o)} data-testid="lp-fallback-toggle">
          <span style={{ ...titleText(wide), flex: 1 }}>{label}</span>
          <span style={{ color: T.text2, fontSize: 15 }} data-testid="lp-fallback-value">{current?.label || ''}</span>
          <span style={{ display: 'inline-flex', color: T.text3, transition: 'transform 150ms', transform: open ? 'rotate(90deg)' : 'none' }}>
            <ChevronIcon />
          </span>
        </button>
        {open && (
          <div role="radiogroup" aria-label={label}>
            {options.map(o => {
              const on = o.id === value;
              return (
                <button key={o.id} type="button" role="radio" aria-checked={on} className="lp-row"
                  onClick={() => { setOpen(false); if (!on) onChoose(o.id); }}
                  style={{ ...rowBtn, ...topBorder, paddingLeft: 28 }}>
                  <span style={{ ...titleText(wide), flex: 1 }}>{o.label}</span>
                  <span style={{ display: 'inline-flex', color: T.accent, width: 20 }}>{on && <CheckIcon />}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>
      {error && <span style={fieldError} role="alert">{error}</span>}
    </>
  );
}

function Segmented({ value, onChange }: { value: Transfer; onChange: (t: Transfer) => void }) {
  const opts: { id: Transfer; label: string }[] = [{ id: 'copy', label: 'Copy' }, { id: 'hardlink', label: 'Hardlink' }];
  return (
    <div role="radiogroup" aria-label="Transfer" style={segment}>
      {opts.map(o => {
        const on = o.id === value;
        return (
          <button key={o.id} type="button" role="radio" aria-checked={on} className="lp-focus" data-testid={`lp-transfer-${o.id}`}
            onClick={() => { if (!on) onChange(o.id); }} style={{ ...segBtn, ...(on ? segOn : null) }}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function MapRow({ row, first, onRemove }: { row: PathMapRow; first: boolean; onRemove: () => void }) {
  return (
    <div data-testid="lp-map-row" style={{ ...rowBtn, cursor: 'default', ...(first ? null : topBorder) }}>
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ ...primaryText, fontFamily: MONO, fontSize: 14 }} title={row.remote}>{row.remote}</span>
        <span style={{ ...secondaryText, fontFamily: MONO }} title={row.local}>{row.local}</span>
      </span>
      <button type="button" aria-label={`Remove ${row.remote}`} onClick={onRemove} style={iconBtn} className="lp-focus">
        <XIcon size={16} />
      </button>
    </div>
  );
}

function MapForm({ index, first, onSave, onCancel }: {
  index: number; first: boolean; onSave: (r: PathMapRow) => Promise<Result>; onCancel: () => void;
}) {
  const [remote, setRemote] = useState('');
  const [local, setLocal] = useState('');
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);
  const k = `path_map.${index}`;
  const ok = !!remote.trim() && !!local.trim();
  return (
    <form data-testid="lp-map-form" style={{ ...formBox, ...(first ? null : topBorder) }}
      onSubmit={async e => {
        e.preventDefault();
        if (!ok || busy) return;
        setBusy(true);
        const r = await onSave({ remote: remote.trim(), local: local.trim() });
        setBusy(false);
        if (!r.ok) setErrors(r.errors);
      }}>
      <Field id="lp-remote" label="qBittorrent path" value={remote} onChange={setRemote} error={errors[`${k}.remote`] || errors.path_map} autoFocus mono />
      <Field id="lp-local" label="Luma path" value={local} onChange={setLocal} error={errors[`${k}.local`]} mono />
      <FormButtons busy={busy} canSave={ok} onCancel={onCancel} />
    </form>
  );
}

// ---------------------------------------------------------------- shared bits

export function Field({ id, label, value, error, onChange, autoFocus, placeholder, maxLength, mono }: {
  id: string; label: string; value: string; error?: string; onChange: (v: string) => void;
  autoFocus?: boolean; placeholder?: string; maxLength?: number; mono?: boolean;
}) {
  return (
    <div>
      <label htmlFor={id} style={labelStyle}>{label}</label>
      <input id={id} data-testid={id} value={value} autoFocus={autoFocus} placeholder={placeholder} maxLength={maxLength}
        autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false}
        aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-error` : undefined}
        onChange={e => onChange(e.target.value)}
        style={{ ...inputStyle, ...(mono ? { fontFamily: MONO, fontSize: 15 } : null), ...(error ? invalid : null) }} />
      {error && <span id={`${id}-error`} style={fieldError}>{error}</span>}
    </div>
  );
}

export function FormButtons({ busy, canSave, onCancel, onDelete }: {
  busy: boolean; canSave: boolean; onCancel: () => void; onDelete?: () => void;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
      {onDelete && (
        <button type="button" onClick={onDelete} disabled={busy} style={dangerBtn} className="lp-focus" data-testid="lp-delete">Delete</button>
      )}
      <span style={{ flex: 1 }} />
      <button type="button" onClick={onCancel} style={ghostBtn} className="lp-focus" data-testid="lp-cancel">Cancel</button>
      <button type="submit" disabled={busy || !canSave} className="lp-focus" data-testid="lp-save"
        style={{ ...primary, opacity: busy || !canSave ? 0.5 : 1, cursor: busy || !canSave ? 'default' : 'pointer' }}>Save</button>
    </div>
  );
}

export function AddRow({ label, first, onClick, testId }: { label: string; first: boolean; onClick: () => void; testId: string }) {
  return (
    <button type="button" className="lp-row" data-testid={testId} onClick={onClick}
      style={{ ...rowBtn, height: 52, color: T.accent, ...(first ? null : topBorder) }}>
      <span style={{ display: 'inline-flex' }}><PlusIcon size={18} /></span>
      <span style={{ fontSize: 16, fontWeight: 500 }}>{label}</span>
    </button>
  );
}

export const libraryPathsCss = `
.lp-row:hover{background:rgba(255,255,255,.04)}
.lp-row:focus-visible,.lp-focus:focus-visible{outline:2px solid #60a5fa;outline-offset:-2px}
`;

const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
export const pad = (wide: boolean): React.CSSProperties => ({ padding: wide ? 0 : '8px 20px 0' });
export const muted: React.CSSProperties = { color: T.text3, fontSize: 17, fontFamily: FONT };
export const sectionLabel: React.CSSProperties = {
  margin: '0 4px 8px', fontSize: 13, fontWeight: 600, color: 'rgba(255,255,255,0.5)', fontFamily: FONT,
};
export const card: React.CSSProperties = {
  borderRadius: 16, background: T.glass, border: `1px solid ${T.glass8}`, overflow: 'hidden',
};
export const topBorder: React.CSSProperties = { borderTop: '1px solid rgba(255,255,255,0.06)' };
export const rowBtn: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 12, width: '100%', minHeight: ROW_H, padding: '10px 16px', border: 'none',
  background: 'none', cursor: 'pointer', fontFamily: FONT, boxSizing: 'border-box', color: T.text, textAlign: 'left',
};
const rowBtnBare: React.CSSProperties = { ...rowBtn, flex: 1, minWidth: 0, width: 'auto', padding: '10px 4px', borderTop: 'none' };
export const titleText = (wide: boolean): React.CSSProperties => ({
  ...primaryText, fontSize: wide ? 15 : 16, fontWeight: 500,
});
export const subText = (wide: boolean): React.CSSProperties => ({
  ...secondaryText, fontSize: wide ? 13 : 14, color: wide ? 'rgba(255,255,255,0.5)' : 'rgba(255,255,255,0.55)',
});
const selectBox: React.CSSProperties = {
  borderRadius: 12, background: 'rgba(255,255,255,0.06)', border: `1px solid ${T.glass12}`, overflow: 'hidden',
};
const selectRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 12, width: '100%', height: 44, padding: '0 14px', border: 'none',
  background: 'none', cursor: 'pointer', fontFamily: FONT, fontSize: 16, color: T.white, boxSizing: 'border-box',
};
const primaryText: React.CSSProperties = {
  fontSize: 16, fontWeight: 500, color: 'rgba(255,255,255,0.95)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
};
const secondaryText: React.CSSProperties = {
  fontSize: 14, color: 'rgba(255,255,255,0.55)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
};
const ruleRow: React.CSSProperties = {
  position: 'relative', display: 'flex', alignItems: 'center', height: ROW_H, padding: '0 12px 0 4px',
  boxSizing: 'border-box', touchAction: 'pan-y', background: 'transparent',
};
const liftedRow: React.CSSProperties = {
  background: 'rgba(59,130,246,0.14)', boxShadow: '0 8px 24px rgba(0,0,0,0.4)', zIndex: 2, borderRadius: 12,
};
const handle: React.CSSProperties = {
  width: 44, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
  border: 'none', background: 'none', color: 'rgba(255,255,255,0.4)', cursor: 'grab', padding: 0, touchAction: 'none', borderRadius: 10,
};
export const formBox: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 16, padding: 16, background: 'rgba(255,255,255,0.03)' };
const labelStyle: React.CSSProperties = {
  display: 'block', margin: '0 0 6px', fontSize: 13, fontWeight: 500, color: T.text2, fontFamily: FONT,
};
const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', height: 44, padding: '0 14px', borderRadius: 12,
  background: 'rgba(255,255,255,0.06)', border: `1px solid ${T.glass12}`, color: T.white, fontSize: 16, fontFamily: FONT, outline: 'none',
  colorScheme: 'dark',
};
const invalid: React.CSSProperties = { border: `1px solid ${T.danger}` };
const fieldError: React.CSSProperties = { display: 'block', marginTop: 6, fontSize: 13, color: T.dangerText, fontFamily: FONT };
export const segment: React.CSSProperties = {
  display: 'flex', padding: 3, borderRadius: 12, background: T.glass, border: `1px solid ${T.glass8}`, maxWidth: 320,
};
export const segBtn: React.CSSProperties = {
  flex: 1, height: 38, border: 'none', borderRadius: 9, background: 'none', color: T.text2,
  fontSize: 15, fontWeight: 500, fontFamily: FONT, cursor: 'pointer',
};
export const segOn: React.CSSProperties = { background: T.accent, color: T.white, fontWeight: 600 };
const iconBtn: React.CSSProperties = {
  width: 44, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
  border: 'none', background: 'none', color: T.text3, cursor: 'pointer', borderRadius: 10,
};
export const primary: React.CSSProperties = {
  minHeight: 44, padding: '0 22px', borderRadius: 10, border: 'none', background: T.accent, color: T.white,
  fontSize: 15, fontWeight: 600, fontFamily: FONT,
};
export const ghostBtn: React.CSSProperties = {
  minHeight: 44, padding: '0 18px', borderRadius: 10, border: `1px solid ${T.glass12}`,
  background: T.glass, color: T.text, fontSize: 15, fontFamily: FONT, cursor: 'pointer',
};
const dangerBtn: React.CSSProperties = {
  minHeight: 44, padding: '0 4px', border: 'none', background: 'none', color: T.dangerText,
  fontSize: 15, fontWeight: 500, fontFamily: FONT, cursor: 'pointer',
};
