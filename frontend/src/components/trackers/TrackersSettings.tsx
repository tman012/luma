/** Settings > Trackers (owner only): the list, the type picker and the
 * add/edit form, per settings-page-spec.md "Settings > Trackers".
 *
 * Routes, relative to `base` (default /settings/trackers):
 *   ''                list
 *   'new'             type picker (desktop: modal over the list; phone: pushed page)
 *   'new?type=<id>'   form for a new tracker of that type
 *   '<id>'            form for a saved tracker
 *
 * Secrets never reach the browser (the API masks them) and are never put
 * back into an input. The section renders nothing for anyone the API does
 * not treat as the signed-in owner. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type React from 'react';
import { useLocation, useNavigate } from 'react-router';
import {
  createTracker, deleteTracker, fetchTrackers, fetchTypes, setTrackerEnabled, testSavedTracker, testTrackerForm,
  updateTracker, SECRET_MASK, UNREACHABLE, type Tracker, type TrackerField, type TrackerType,
} from './trackersApi';
import {
  initialForm, isDirty, keptSecrets, splitServerErrors, toPayload, validate, UNREADABLE,
  type Errors, type FormState,
} from './trackerForm';
import {
  BLUE_HOVER, ChevronLeft, ChevronRight, CheckMark, CloseIcon, circle, circleHit, ConfirmDialog, DANGER,
  DANGER_TEXT, DIVIDER, EyeIcon, EyeOffIcon, FONT, GLASS, GLASS_BORDER, Modal, PhoneHeader, PlusIcon, primaryBtn,
  SearchIcon, secondaryBtn, Spinner, srOnly, SUCCESS, Switch, TEXT, TEXT_2, TEXT_3, TopToast, trackersCss,
  dangerTextBtn,
} from './trackersUi';

export const TRACKERS_BASE = '/settings/trackers';
export const SAVED = 'Saved';
export const SAVE_FAILED = "Couldn't save";
export const LOAD_FAILED = "Couldn't load trackers";
export const SEARCH_FROM = 8;          // the picker gets a search field above this many types
export const TEST_STATUS_MS = 4000;
export const CHIP_PHONE_H = 44;       // phone: every tap target is at least 44px

type Access = 'unknown' | 'owner' | 'denied';

/** Whether the signed-in user may use Settings > Trackers: null until known,
 * then true only for the owner with a session (the API answers 401/403
 * otherwise). Pass enabled=false to skip the check (not an admin, no session). */
/** Retry delays (ms) for a transient answer (429, 5xx, unreachable). */
export const ACCESS_RETRY_MS = [1000, 3000, 8000];

export function useTrackersAccess(enabled: boolean, retryMs: number[] = ACCESS_RETRY_MS): boolean | null {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setOk(null);
    const attempt = (n: number) => {
      fetchTypes().then(r => {
        if (!alive) return;
        if (r.ok) { setOk(Array.isArray(r.data.types)); return; }
        const transient = r.status === 0 || r.status === 429 || r.status >= 500;
        if (!transient) { setOk(false); return; }       // 401/403/404: not the owner
        // A busy or restarting server must not hide Trackers: retry, then show
        // the section anyway (its own list load shows the error and Retry).
        if (n < retryMs.length) timer = setTimeout(() => attempt(n + 1), retryMs[n]);
        else setOk(true);
      });
    };
    attempt(0);
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [enabled, retryMs]);
  return enabled ? ok : false;
}

/** Splits the path under `base` into the current view. */
function useView(base: string) {
  const { pathname, search } = useLocation();
  const rest = pathname.startsWith(base) ? pathname.slice(base.length).replace(/^\/+|\/+$/g, '') : '';
  const typeId = new URLSearchParams(search).get('type') || '';
  if (!rest) return { kind: 'list' as const };
  if (rest === 'new') return typeId ? { kind: 'new' as const, typeId } : { kind: 'pick' as const };
  if (/^\d+$/.test(rest)) return { kind: 'edit' as const, id: Number(rest) };
  return { kind: 'unknown' as const };
}

export interface TrackersSettingsProps {
  /** Desktop layout (>=1024px) vs the phone sheet. */
  wide: boolean;
  /** Route of the list. */
  base?: string;
  /** Phone: the list's back button (to the Settings list). */
  onExit?: () => void;
}

export function TrackersSettings({ wide, base = TRACKERS_BASE, onExit }: TrackersSettingsProps) {
  const navigate = useNavigate();
  const view = useView(base);
  const [access, setAccess] = useState<Access>('unknown');
  const [types, setTypes] = useState<TrackerType[] | null>(null);
  const [trackers, setTrackers] = useState<Tracker[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [toast, setToast] = useState<{ text: string; n: number } | null>(null);
  const seq = useRef(0);

  const show = useCallback((text: string) => setToast(t => ({ text, n: (t?.n || 0) + 1 })), []);

  const loadTrackers = useCallback(async () => {
    const n = ++seq.current;
    const r = await fetchTrackers();
    if (n !== seq.current) return;
    if (r.status === 401 || r.status === 403) { setAccess('denied'); return; }
    if (!r.ok || !Array.isArray(r.data.trackers)) { setLoadError(true); return; }
    setLoadError(false);
    setTrackers(r.data.trackers);
  }, []);

  const load = useCallback(async () => {
    setLoadError(false);
    const [t] = await Promise.all([fetchTypes(), loadTrackers()]);
    if (t.status === 401 || t.status === 403) { setAccess('denied'); return; }
    if (!t.ok || !Array.isArray(t.data.types)) { setLoadError(true); return; }
    setTypes(t.data.types);
    setAccess(a => (a === 'denied' ? a : 'owner'));
  }, [loadTrackers]);

  useEffect(() => { load(); }, [load]);

  const toList = useCallback(() => navigate(base), [navigate, base]);

  const toggle = useCallback(async (t: Tracker, enabled: boolean) => {
    seq.current++;          // a list load in flight must not undo this
    setTrackers(list => list && list.map(x => (x.id === t.id ? { ...x, enabled } : x)));
    const r = await setTrackerEnabled(t.id, enabled);
    if (r.ok && r.data.tracker) {
      const saved = r.data.tracker;
      setTrackers(list => list && list.map(x => (x.id === saved.id ? saved : x)));
    } else {
      setTrackers(list => list && list.map(x => (x.id === t.id ? { ...x, enabled: t.enabled } : x)));
      if (r.status === 404) loadTrackers();
      else show(SAVE_FAILED);
    }
  }, [loadTrackers, show]);

  if (access === 'denied') return null;

  const toastEl = toast && <TopToast key={toast.n} text={toast.text} onDone={() => setToast(null)} />;
  const shell = (body: React.ReactNode) => (
    <>
      <style>{trackersCss}</style>
      {toastEl}
      <section data-testid="trackers-section" data-view={view.kind} style={{ fontFamily: FONT, color: TEXT }}>{body}</section>
    </>
  );

  if (loadError && (!types || !trackers)) {
    return shell(
      <>
        {wide ? <ListTitle onAdd={null} /> : <PhoneHeader title="Trackers" onBack={onExit || (() => navigate('/settings'))} />}
        <div style={{ ...emptyRow(), justifyContent: 'space-between' }} data-testid="trackers-load-error">
          <span style={{ color: TEXT_3, fontSize: wide ? 15 : 17 }}>{LOAD_FAILED}</span>
          <button type="button" className="lt-btn lt-focus" style={{ ...dangerTextBtn, color: BLUE_HOVER }} onClick={load}>Retry</button>
        </div>
      </>,
    );
  }
  if (!types || !trackers) return shell(null);

  if (view.kind === 'unknown') return shell(<Redirect to={base} />);

  if (view.kind === 'new' || view.kind === 'edit') {
    const tracker = view.kind === 'edit' ? trackers.find(t => t.id === view.id) || null : null;
    const type = types.find(t => t.id === (tracker ? tracker.type : view.kind === 'new' ? view.typeId : ''));
    if (!type || (view.kind === 'edit' && !tracker)) return shell(<Redirect to={view.kind === 'new' ? `${base}/new` : base} />);
    return shell(
      <TrackerForm key={tracker ? `edit-${tracker.id}` : `new-${type.id}`} wide={wide} type={type} tracker={tracker}
        onBack={toList}
        onSaved={saved => {
          if (saved) setTrackers(list => list && (list.some(x => x.id === saved.id) ? list.map(x => (x.id === saved.id ? saved : x)) : [...list, saved]));
          show(SAVED); loadTrackers(); toList();
        }}
        onDeleted={() => { if (tracker) setTrackers(list => list && list.filter(x => x.id !== tracker.id)); loadTrackers(); toList(); }}
        onGone={() => { loadTrackers(); toList(); }}
        onTested={() => loadTrackers()} />,
    );
  }

  const openAdd = () => navigate(`${base}/new`);
  const pick = (t: TrackerType) => navigate(`${base}/new?type=${encodeURIComponent(t.id)}`);
  if (view.kind === 'pick' && !wide) {
    return shell(<TypePicker wide={false} types={types} onPick={pick} onClose={toList} />);
  }
  return shell(
    <>
      <TrackerList wide={wide} trackers={trackers} onAdd={openAdd} onOpen={t => navigate(`${base}/${t.id}`)}
        onToggle={toggle} onExit={onExit || (() => navigate('/settings'))} />
      {view.kind === 'pick' && <TypePicker wide types={types} onPick={pick} onClose={toList} />}
    </>,
  );
}

function Redirect({ to }: { to: string }) {
  const navigate = useNavigate();
  useEffect(() => { navigate(to, { replace: true }); }, [navigate, to]);
  return null;
}

// ---------------------------------------------------------------- list

function ListTitle({ onAdd }: { onAdd: (() => void) | null }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, margin: '0 0 16px' }}>
      <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600, lineHeight: '40px', color: TEXT, fontFamily: FONT }}>Trackers</h2>
      {onAdd && (
        <button type="button" className="lt-btn lt-primary" style={{ ...primaryBtn, height: 40, padding: '0 16px 0 12px' }}
          onClick={onAdd} data-testid="tracker-add">
          <PlusIcon size={18} />Add tracker
        </button>
      )}
    </div>
  );
}

function dotColor(t: Tracker): string {
  if (!t.enabled) return 'rgba(255,255,255,0.3)';
  if (t.status?.state === 'ok') return SUCCESS;
  if (t.status?.state === 'error') return DANGER;
  return 'rgba(255,255,255,0.3)';
}

function statusText(t: Tracker): string {
  if (t.status?.state === 'ok') return 'Connected';
  if (t.status?.state === 'error') return t.status.message || 'Failing';
  return '';
}

function emptyRow(): React.CSSProperties {
  return { height: 56, display: 'flex', alignItems: 'center', padding: '0 20px', boxSizing: 'border-box' };
}

function TrackerList({ wide, trackers, onAdd, onOpen, onToggle, onExit }: {
  wide: boolean; trackers: Tracker[]; onAdd: () => void; onOpen: (t: Tracker) => void;
  onToggle: (t: Tracker, on: boolean) => void; onExit: () => void;
}) {
  const rows = trackers.length === 0 ? (
    <div style={emptyRow()} data-testid="trackers-empty">
      <span style={{ color: TEXT_3, fontSize: wide ? 15 : 17 }}>No trackers</span>
    </div>
  ) : (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0 }} data-testid="trackers-list">
      {trackers.map((t, i) => {
        const status = statusText(t);
        return (
          <li key={t.id} data-testid="tracker-row" data-enabled={t.enabled ? 'true' : 'false'}
            style={{ position: 'relative', display: 'flex', alignItems: 'center', height: 56 }}>
            <button type="button" className="lt-row" onClick={() => onOpen(t)}
              style={{ flex: 1, minWidth: 0, height: '100%', display: 'flex', alignItems: 'center', gap: 12,
                padding: wide ? '0 8px 0 20px' : '0 8px 0 20px', border: 'none', background: 'none', cursor: 'pointer',
                fontFamily: FONT, textAlign: 'left', color: TEXT }}>
              <span aria-hidden="true" data-testid="tracker-dot" data-state={t.enabled ? t.status?.state : 'off'}
                style={{ width: 8, height: 8, borderRadius: 4, flex: 'none', background: dotColor(t) }} />
              <span style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 10 }}>
                <span data-testid="tracker-name" style={{ minWidth: 0, fontSize: wide ? 15 : 17, fontWeight: wide ? 500 : 400,
                  color: t.enabled ? TEXT : TEXT_3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.name}</span>
                <span data-testid="tracker-type" style={{ flex: 'none', fontSize: 13, color: TEXT_3 }}>{t.type_name}</span>
              </span>
              {status && <span style={srOnly}>{status}</span>}
            </button>
            <span style={{ paddingRight: wide ? 20 : 20 - 4, display: 'inline-flex' }}>
              <Switch small={wide} on={t.enabled} label={t.name} onChange={on => onToggle(t, on)} />
            </span>
            {i < trackers.length - 1 && (
              <span aria-hidden="true" style={{ position: 'absolute', left: 20, right: wide ? 0 : 20, bottom: 0, height: 1,
                background: DIVIDER, ...(wide ? { left: 0 } : null) }} />
            )}
          </li>
        );
      })}
    </ul>
  );
  if (wide) {
    return (
      <div data-testid="trackers-list-view">
        <ListTitle onAdd={onAdd} />
        <div style={{ borderRadius: 16, background: GLASS, border: GLASS_BORDER, overflow: 'hidden' }} data-testid="trackers-card">
          {rows}
        </div>
      </div>
    );
  }
  return (
    <div data-testid="trackers-list-view">
      <PhoneHeader title="Trackers" onBack={onExit} action={{ label: 'Add tracker', icon: <PlusIcon size={18} />, onClick: onAdd }} />
      <div style={{ marginTop: 8 }}>{rows}</div>
    </div>
  );
}

// ---------------------------------------------------------------- type picker

function TypePicker({ wide, types, onPick, onClose }: {
  wide: boolean; types: TrackerType[]; onPick: (t: TrackerType) => void; onClose: () => void;
}) {
  const [q, setQ] = useState('');
  const searchable = types.length > SEARCH_FROM;
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? types.filter(t => t.name.toLowerCase().includes(s)) : types;
  }, [q, types]);
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (wide) searchRef.current?.focus(); }, [wide]);

  const body = (
    <>
      {searchable && (
        <div style={{ position: 'relative', margin: wide ? '0 24px 8px' : '8px 20px 8px' }}>
          <span style={{ position: 'absolute', left: 12, top: 11, color: TEXT_3, display: 'inline-flex' }}><SearchIcon size={18} /></span>
          <input ref={searchRef} type="search" className="lt-input lt-search" placeholder="Search" aria-label="Search"
            value={q} onChange={e => setQ(e.target.value)} data-testid="tracker-type-search"
            autoComplete="off" spellCheck={false} />
        </div>
      )}
      <ul style={{ listStyle: 'none', margin: 0, padding: wide ? '0 0 12px' : 0, overflowY: 'auto' }} data-testid="tracker-types">
        {shown.map((t, i) => (
          <li key={t.id} style={{ position: 'relative' }}>
            <button type="button" className="lt-pick" onClick={() => onPick(t)} data-testid="tracker-type"
              style={{ width: '100%', height: 56, display: 'flex', alignItems: 'center', gap: 12, padding: wide ? '0 24px' : '0 20px',
                border: 'none', background: 'none', cursor: 'pointer', fontFamily: FONT, color: TEXT, textAlign: 'left',
                fontSize: 16, fontWeight: 500, boxSizing: 'border-box' }}>
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.name}</span>
              <span style={{ display: 'inline-flex', color: 'rgba(255,255,255,0.3)' }}><ChevronRight size={18} /></span>
            </button>
            {i < shown.length - 1 && (
              <span aria-hidden="true" style={{ position: 'absolute', left: wide ? 24 : 20, right: wide ? 24 : 20, bottom: 0, height: 1, background: DIVIDER }} />
            )}
          </li>
        ))}
        {shown.length === 0 && (
          <li style={{ height: 56, display: 'flex', alignItems: 'center', padding: wide ? '0 24px' : '0 20px', color: TEXT_3, fontSize: 16 }}>No results</li>
        )}
      </ul>
    </>
  );
  if (!wide) {
    return (
      <div data-testid="tracker-type-picker">
        <PhoneHeader title="Add tracker" onBack={onClose} />
        {body}
      </div>
    );
  }
  return (
    <Modal wide width={480} label="Add tracker" onClose={onClose} testId="tracker-type-picker">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 12px 8px 24px' }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: TEXT }}>Add tracker</h2>
        <button type="button" aria-label="Close" onClick={onClose} style={circleHit} className="lt-focus">
          <span style={circle}><CloseIcon size={18} /></span>
        </button>
      </div>
      {body}
    </Modal>
  );
}

// ---------------------------------------------------------------- form

interface TestResult { ok: boolean; text: string }

function TrackerForm({ wide, type, tracker, onBack, onSaved, onDeleted, onGone, onTested }: {
  wide: boolean; type: TrackerType; tracker: Tracker | null;
  onBack: () => void; onSaved: (t: Tracker | null) => void; onDeleted: () => void; onGone: () => void; onTested: () => void;
}) {
  // The form opens with the tracker as it was; a status refresh after Test must not reset it.
  const [start] = useState(() => initialForm(type, tracker));
  const [form, setForm] = useState<FormState>(start);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [serverErrors, setServerErrors] = useState<Errors>({});
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  // "Connected" fades after 4 s; a failed Test stays until the next edit or Test.
  const [result, setResult] = useState<TestResult | null>(null);
  useEffect(() => {
    if (!result?.ok) return;
    const t = window.setTimeout(() => setResult(null), TEST_STATUS_MS);
    return () => window.clearTimeout(t);
  }, [result]);
  const [confirm, setConfirm] = useState<'discard' | 'delete' | null>(null);
  const [deleting, setDeleting] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const errors = useMemo(() => validate(type, form, tracker), [type, form, tracker]);
  const dirty = isDirty(type, form, start, tracker);
  const valid = Object.keys(errors).length === 0;
  const kept = keptSecrets(type, form, tracker);
  const unreadable = !!tracker && tracker.status?.state === 'error' && tracker.status.message === UNREADABLE;
  const title = tracker ? tracker.name : type.name;

  const shownError = (key: string): string => {
    if (serverErrors[key]) return serverErrors[key]!;
    if (touched[key] && errors[key]) return errors[key]!;
    return '';
  };
  const clearServer = (key: string) => {
    setServerErrors(e => {
      if (!(key in e) && !(key === 'url')) return e;
      const next = { ...e };
      delete next[key];
      // A new URL drops stored secrets, so their server errors are stale too.
      if (key === 'url') for (const f of type.fields) if (f.secret) delete next[f.key];
      return next;
    });
    setFormError('');
    setResult(r => (r && !r.ok ? null : r));
  };
  const setValue = (key: string, v: string) => { setForm(f => ({ ...f, values: { ...f.values, [key]: v } })); clearServer(key); };
  const setSecret = (key: string, v: string) => {
    setForm(f => ({ ...f, secrets: { ...f.secrets, [key]: { value: v, touched: true } } }));
    clearServer(key);
  };
  const toggleCategory = (id: number) => {
    setForm(f => ({ ...f, categories: f.categories.includes(id) ? f.categories.filter(c => c !== id) : [...f.categories, id] }));
    setTouched(t => ({ ...t, categories: true }));
    setResult(r => (r && !r.ok ? null : r));
    clearServer('categories');
  };
  const blur = (key: string) => setTouched(t => ({ ...t, [key]: true }));
  const touchAll = () => setTouched(Object.fromEntries(type.fields.map(f => [f.key, true])));

  const applyServer = (fields: Record<string, string> | undefined, fallback: string) => {
    const split = splitServerErrors(type, fields);
    setServerErrors(split.fields);
    setFormError(split.form || (Object.keys(split.fields).length ? '' : fallback));
  };

  const save = async () => {
    if (!valid) { touchAll(); return; }
    if (!dirty || saving) return;
    setSaving(true);
    setFormError('');
    const payload = toPayload(type, form, tracker);
    const r = tracker ? await updateTracker(tracker.id, payload) : await createTracker(payload);
    if (!alive.current) return;
    setSaving(false);
    if (r.ok) { onSaved(r.data.tracker || null); return; }
    if (r.status === 404) { onGone(); return; }
    if (r.status === 409) { setFormError(r.data.error || UNREADABLE); return; }
    if (r.status === 400 && r.data.fields) { applyServer(r.data.fields, SAVE_FAILED); return; }
    // 403 (owner access, session) and 429 (rate limited): the server's own words.
    if ((r.status === 403 || r.status === 429) && r.data.error) { setFormError(r.data.error); return; }
    setFormError(r.status === 0 ? UNREACHABLE : SAVE_FAILED);
  };

  const test = async () => {
    if (testing) return;
    if (!valid) { touchAll(); return; }
    setTesting(true);
    setResult(null);
    const r = tracker && !dirty
      ? await testSavedTracker(tracker.id)
      : await testTrackerForm({ ...toPayload(type, form, tracker), ...(tracker ? { id: tracker.id } : null) });
    if (!alive.current) return;
    setTesting(false);
    if (r.status === 404) { onGone(); return; }
    if (r.status === 400 && r.data.fields) {
      applyServer(r.data.fields, '');
      const split = splitServerErrors(type, r.data.fields);
      if (split.form) setResult({ ok: false, text: split.form });
      return;
    }
    if (r.ok && r.data.ok) setResult({ ok: true, text: r.data.message || 'Connected' });
    else setResult({ ok: false, text: r.data.error || (r.status === 0 ? UNREACHABLE : 'Unexpected response') });
    if (tracker && !dirty && r.ok) onTested();
  };

  const back = () => { if (dirty) setConfirm('discard'); else onBack(); };

  const remove = async () => {
    if (!tracker) return;
    setDeleting(true);
    const r = await deleteTracker(tracker.id);
    if (!alive.current) return;
    setDeleting(false);
    setConfirm(null);
    if (r.ok || r.status === 404) { onDeleted(); return; }
    setFormError(r.status === 0 ? UNREACHABLE : "Couldn't delete");
  };

  const canSave = valid && dirty && !saving;

  const fields = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', minHeight: 44 }}>
        <span id="tracker-enabled-label" style={{ fontSize: wide ? 15 : 17, fontWeight: wide ? 500 : 400, color: TEXT }}>Enabled</span>
        <Switch small={wide} on={form.enabled} label="Enabled" onChange={on => { setForm(f => ({ ...f, enabled: on })); setResult(r => (r && !r.ok ? null : r)); }} />
      </div>
      {type.fields.map(f => (
        <Field key={f.key} wide={wide} field={f} type={type} form={form} error={shownError(f.key)}
          kept={kept.has(f.key)} unreadable={unreadable && f.secret && tracker?.[f.key] === SECRET_MASK}
          onValue={setValue} onSecret={setSecret} onCategory={toggleCategory} onBlur={blur} />
      ))}
    </div>
  );

  const status = (
    <span role="status" aria-live="polite" data-testid="tracker-test-result"
      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, minHeight: 20,
        color: result ? (result.ok ? SUCCESS : DANGER_TEXT) : 'transparent' }}>
      {result && (result.ok ? <CheckMark size={16} /> : <CloseIcon size={16} />)}
      {result?.text}
    </span>
  );
  const formErr = formError ? (
    <span role="alert" data-testid="tracker-form-error" style={{ fontSize: 13, color: DANGER_TEXT }}>{formError}</span>
  ) : null;

  const testBtn = (
    <button type="button" className="lt-btn lt-ghost" style={{ ...secondaryBtn, position: 'relative', ...(wide ? null : { width: '100%' }) }}
      onClick={test} disabled={testing} aria-busy={testing} data-testid="tracker-test">
      <span style={{ visibility: testing ? 'hidden' : 'visible' }}>Test</span>
      {testing && <span style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spinner /></span>}
    </button>
  );
  const saveBtn = (
    <button type="button" className="lt-btn lt-primary" style={{ ...primaryBtn, ...(wide ? null : { width: '100%' }) }}
      onClick={save} disabled={!canSave} data-testid="tracker-save">Save</button>
  );

  const actions = wide ? (
    <div style={{ marginTop: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        {testBtn}
        {saveBtn}
        {/* 12px from Save comes from the row gap; no extra margin. */}
        <span style={{ display: 'inline-flex', minWidth: 0 }} data-testid="tracker-test-slot">{status}</span>
        {tracker && (
          <button type="button" className="lt-btn lt-focus" style={{ ...dangerTextBtn, marginLeft: 'auto' }}
            onClick={() => setConfirm('delete')} data-testid="tracker-delete">Delete</button>
        )}
      </div>
      {formErr && <div style={{ marginTop: 8 }}>{formErr}</div>}
    </div>
  ) : (
    <div style={{ marginTop: 24, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {saveBtn}
      {testBtn}
      {/* Phone: the Test result has its own line under the buttons. */}
      <div data-testid="tracker-test-slot" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, width: '100%' }}>{status}{formErr}</div>
      {tracker && (
        <button type="button" className="lt-btn lt-row" onClick={() => setConfirm('delete')} data-testid="tracker-delete"
          style={{ marginTop: 24, height: 56, width: '100%', border: 'none', background: 'none', color: DANGER, fontSize: 17,
            fontFamily: FONT, textAlign: 'left', padding: 0, cursor: 'pointer', borderTop: `1px solid ${DIVIDER}` }}>
          Delete
        </button>
      )}
    </div>
  );

  const dialogs = (
    <>
      {confirm === 'discard' && (
        <ConfirmDialog wide={wide} title="Discard changes?" confirm="Discard" cancel="Keep editing"
          onConfirm={() => { setConfirm(null); onBack(); }} onCancel={() => setConfirm(null)} />
      )}
      {confirm === 'delete' && tracker && (
        <ConfirmDialog wide={wide} title={`Delete ${tracker.name}?`} confirm="Delete" cancel="Cancel" busy={deleting}
          onConfirm={remove} onCancel={() => setConfirm(null)} />
      )}
    </>
  );

  const onSubmit = (e: React.FormEvent) => { e.preventDefault(); save(); };

  if (wide) {
    return (
      <form data-testid="tracker-form" onSubmit={onSubmit} noValidate style={{ maxWidth: 720 }}>
        <button type="button" onClick={back} className="lt-btn lt-focus" data-testid="tracker-back"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, height: 32, padding: '0 8px 0 2px', margin: '4px 0 4px -2px',
            border: 'none', background: 'none', color: BLUE_HOVER, fontSize: 15, fontWeight: 500, fontFamily: FONT,
            cursor: 'pointer', borderRadius: 8 }}>
          <ChevronLeft size={18} />Trackers
        </button>
        <h2 style={{ margin: '0 0 20px', fontSize: 20, fontWeight: 600, lineHeight: '32px', color: TEXT,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</h2>
        {fields}
        {actions}
        <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
        {dialogs}
      </form>
    );
  }
  return (
    <form data-testid="tracker-form" onSubmit={onSubmit} noValidate>
      <PhoneHeader title={title} onBack={back} />
      <div style={{ padding: '8px 20px 24px' }}>
        {fields}
        {actions}
      </div>
      <button type="submit" hidden tabIndex={-1} aria-hidden="true" />
      {dialogs}
    </form>
  );
}

// ---------------------------------------------------------------- fields

const labelStyle: React.CSSProperties = {
  display: 'block', margin: '0 0 6px', fontSize: 13, fontWeight: 600, color: TEXT_2, fontFamily: FONT,
};

function FieldError({ id, text }: { id: string; text: string }) {
  if (!text) return null;
  return (
    <span id={id} data-testid={`${id}`} style={{ display: 'block', marginTop: 6, fontSize: 13, color: DANGER_TEXT,
      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{text}</span>
  );
}

function Field({ wide, field, type, form, error, kept, unreadable, onValue, onSecret, onCategory, onBlur }: {
  wide: boolean; field: TrackerField; type: TrackerType; form: FormState; error: string; kept: boolean; unreadable: boolean;
  onValue: (k: string, v: string) => void; onSecret: (k: string, v: string) => void; onCategory: (id: number) => void;
  onBlur: (k: string) => void;
}) {
  const id = `tracker-${field.key}`;
  const errId = `${id}-error`;
  const shown = error || (unreadable ? UNREADABLE : '');
  const common = {
    id, 'aria-invalid': shown ? true : undefined, 'aria-describedby': shown ? errId : undefined,
    'aria-required': field.required || undefined, 'data-testid': id, className: 'lt-input',
    onBlur: () => onBlur(field.key), autoComplete: 'off', spellCheck: false,
  } as const;

  if (field.kind === 'categories') {
    return (
      <div role="group" aria-labelledby={`${id}-label`} data-testid={id}>
        <span id={`${id}-label`} style={labelStyle}>{field.label}</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {type.categories.map(c => {
            const on = form.categories.includes(c.id);
            return (
              <button key={c.id} type="button" className="lt-chip lt-btn" aria-pressed={on} onClick={() => onCategory(c.id)}
                data-testid="tracker-category"
                style={{ height: wide ? 36 : CHIP_PHONE_H, padding: '0 14px', borderRadius: wide ? 18 : CHIP_PHONE_H / 2, fontFamily: FONT, fontSize: 14, fontWeight: 500,
                  cursor: 'pointer', border: on ? '1px solid rgba(59,130,246,0.35)' : '1px solid rgba(255,255,255,0.12)',
                  background: on ? 'rgba(59,130,246,0.18)' : GLASS, color: on ? BLUE_HOVER : TEXT_2 }}>
                {c.name}
              </button>
            );
          })}
        </div>
        <FieldError id={errId} text={shown} />
      </div>
    );
  }

  if (field.secret) return <SecretField field={field} form={form} kept={kept} error={shown} common={common} errId={errId} onSecret={onSecret} />;

  return (
    <div>
      <label htmlFor={id} style={labelStyle}>{field.label}</label>
      <input {...common}
        type={field.kind === 'url' ? 'url' : field.kind === 'number' ? 'number' : 'text'}
        inputMode={field.kind === 'number' ? 'numeric' : field.kind === 'url' ? 'url' : undefined}
        min={field.kind === 'number' ? 1 : undefined} max={field.kind === 'number' ? 50 : undefined}
        autoCapitalize="off" autoCorrect="off"
        value={form.values[field.key] ?? ''} onChange={e => onValue(field.key, e.target.value)} />
      <FieldError id={errId} text={shown} />
    </div>
  );
}

/** A masked input. A stored value shows only as a dotted placeholder and is
 * never put in the input; the eye shows what was typed in this form. */
function SecretField({ field, form, kept, error, common, errId, onSecret }: {
  field: TrackerField; form: FormState; kept: boolean; error: string; errId: string;
  common: Record<string, unknown>; onSecret: (k: string, v: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  const s = form.secrets[field.key] || { value: '', touched: false };
  // An optional stored secret emptied on purpose is cleared on Save: no dots then.
  const dots = kept && !(s.touched && !field.required && s.value === '');
  return (
    <div>
      <label htmlFor={common.id as string} style={labelStyle}>{field.label}</label>
      <div style={{ position: 'relative' }}>
        <input {...common} type={visible ? 'text' : 'password'} autoComplete="new-password" autoCapitalize="off"
          data-secret="true" style={{ paddingRight: 52 }}
          placeholder={dots ? '••••••••' : ''} value={s.value} onChange={e => onSecret(field.key, e.target.value)} />
        <button type="button" aria-label={visible ? `Hide ${field.label}` : `Show ${field.label}`} aria-pressed={visible}
          onClick={() => setVisible(v => !v)} className="lt-focus" data-testid={`${common.id as string}-eye`}
          style={{ position: 'absolute', right: 0, top: 0, width: 44, height: 44, display: 'inline-flex', alignItems: 'center',
            justifyContent: 'center', border: 'none', background: 'none', color: TEXT_3, cursor: 'pointer', borderRadius: 10 }}>
          {visible ? <EyeOffIcon size={20} /> : <EyeIcon size={20} />}
        </button>
      </div>
      <FieldError id={errId} text={error} />
    </div>
  );
}

