/** Settings > TV (owner only, luma-test builds): TV library paths, Scan, the
 * matched shows ("Wrong show?"), unmatched folders ("Match"), and Upgrades.
 * Scanning only reads the library folders; everything goes to tv.db. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { T } from '@/theme/tokens';
import { FONT } from '@/components/start/StartUI';
import { NoticeToast } from '@/components/start/SetupToast';
import { ChevronIcon } from '@/components/nav/AppNav';
import { trackersCss } from '@/components/trackers/trackersUi';
import {
  AddRow, Field, PathForm, PathRow, WideCtx, card, formBox, ghostBtn, libraryPathsCss, muted, pad, primary, rowBtn,
  sectionLabel, segBtn, segOn, segment, subText, titleText, topBorder,
} from './LibraryPathsSettings';
import { SAVE_FAILED } from './libraryPathsApi';
import {
  addTvPath, deleteTvPath, editTvPath, showSubtitle, lastScanText, loadTv, loadTvLibrary, matchFolder, needsReview, rematchShow,
  saveUpgrades, searchShows, showLabel, startScan,
  type Candidate, type Show, type TvLibrary, type TvSettingsView, type Unmatched, type UpgradeMode,
} from './tvLibraryApi';

export const POLL_MS = 2000;
/** Space between sections (Library paths, Scan, Unmatched, Shows, Upgrades), as on Library paths. */
export const SECTION_GAP = 28;
type Editing = { kind: 'path'; id: string | null } | { kind: 'show'; id: number } | { kind: 'folder'; id: number } | null;

export function TvLibrarySettings({ wide }: { wide: boolean }) {
  const [view, setView] = useState<TvSettingsView | null>(null);
  const [lib, setLib] = useState<TvLibrary | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [toast, setToast] = useState('');
  const [editing, setEditing] = useState<Editing>(null);
  const seq = useRef(0);

  const refreshLib = useCallback(() => loadTvLibrary().then(r => { if (r.ok) setLib(r.data); return r; }), []);
  useEffect(() => {
    loadTv().then(r => (r.ok ? setView(r.data) : setLoadFailed(true)));
    refreshLib();
  }, [refreshLib]);

  const running = !!lib?.scan.running;
  useEffect(() => {          // follow a running scan
    if (!running) return undefined;
    const t = setInterval(refreshLib, POLL_MS);
    return () => clearInterval(t);
  }, [running, refreshLib]);

  if (!view) {
    return loadFailed ? <div role="alert" style={{ ...pad(wide), ...muted }}>{"Couldn't load TV settings"}</div> : null;
  }
  const say = (msg: string) => { seq.current += 1; setToast(msg); };
  const donePath = (r: { ok: true; data: TvSettingsView } | { ok: false; errors: Record<string, string>; error: string }, msg: string) => {
    if (r.ok) { setView(r.data); setEditing(null); say(msg); refreshLib(); return { ok: true }; }
    return { ok: false, errors: r.errors };
  };
  const doneMatch = (r: { ok: true; data: TvLibrary } | { ok: false; error: string }) => {
    if (r.ok) { setLib(r.data); setEditing(null); say('Matched'); } else say(r.error || SAVE_FAILED);
  };

  return (
    <WideCtx.Provider value={wide}>
    <section data-testid="tv-section" style={{ ...pad(wide), display: 'flex', flexDirection: 'column', gap: SECTION_GAP, fontFamily: FONT }}>
      <style>{libraryPathsCss}</style>
      <style>{trackersCss}</style>
      {toast && <NoticeToast key={toast + seq.current} text={toast} onDone={() => setToast('')} />}

      <div data-testid="tv-paths">
        <h3 style={sectionLabel}>Library paths</h3>
        <div style={card}>
          {view.paths.map((p, i) => (editing?.kind === 'path' && editing.id === p.id
            ? <PathForm key={p.id} path={p} first={i === 0} onCancel={() => setEditing(null)}
                onSave={(name, path) => editTvPath(p.id, name, path).then(r => donePath(r, 'Saved'))}
                onDelete={() => deleteTvPath(p.id).then(r => {
                  if (!r.ok && !Object.keys(r.errors).length) say(r.error);
                  return donePath(r, 'Removed');
                })} />
            : <PathRow key={p.id} path={p} first={i === 0} onOpen={() => setEditing({ kind: 'path', id: p.id })} />))}
          {editing?.kind === 'path' && editing.id === null
            ? <PathForm first={view.paths.length === 0} onCancel={() => setEditing(null)}
                onSave={(name, path) => addTvPath(name, path).then(r => donePath(r, 'Added'))} />
            : <AddRow label="Add path" first={view.paths.length === 0} testId="tv-add-path"
                onClick={() => setEditing({ kind: 'path', id: null })} />}
        </div>
      </div>

      {view.paths.length > 0 && (
        <div data-testid="tv-scan-row" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <button type="button" className="lp-focus" data-testid="tv-scan" disabled={running}
            onClick={() => startScan().then(r => {
              if (r.ok) setLib(l => (l ? { ...l, scan: r.data.scan } : l)); else say(r.error);
              refreshLib();
            })}
            style={{ ...primary, opacity: running ? 0.5 : 1, cursor: running ? 'default' : 'pointer' }}>
            {running ? 'Scanning…' : 'Scan'}
          </button>
          <span data-testid="tv-scan-status" role="status"
            style={{ fontSize: 14, color: lib?.scan.error ? T.dangerText : 'rgba(255,255,255,0.55)' }}>
            {lib?.scan.error || (lib ? lastScanText(lib.scan.last_scan) : '')}
          </span>
        </div>
      )}

      {lib && lib.unmatched.length > 0 && (
        <div data-testid="tv-unmatched">
          <h3 style={sectionLabel}>Unmatched</h3>
          <div style={card}>
            {lib.unmatched.map((u, i) => (
              <div key={u.id}>
                <UnmatchedRow u={u} first={i === 0} open={editing?.kind === 'folder' && editing.id === u.id}
                  onOpen={() => setEditing(editing?.kind === 'folder' && editing.id === u.id ? null : { kind: 'folder', id: u.id })} />
                {editing?.kind === 'folder' && editing.id === u.id && (
                  <MatchPicker initial={u.guess_title || u.folder} year={u.guess_year} suggestions={u.candidates}
                    onCancel={() => setEditing(null)} onPick={c => matchFolder(u.id, c.tmdb_id).then(doneMatch)} />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {lib && lib.shows.length > 0 && (
        <div data-testid="tv-shows">
          <h3 style={sectionLabel}>Shows</h3>
          <div style={card}>
            {lib.shows.map((s, i) => (
              <div key={s.id}>
                <ShowRow s={s} first={i === 0} open={editing?.kind === 'show' && editing.id === s.id}
                  onOpen={() => setEditing(editing?.kind === 'show' && editing.id === s.id ? null : { kind: 'show', id: s.id })} />
                {editing?.kind === 'show' && editing.id === s.id && (
                  <MatchPicker initial={s.title} year={null} suggestions={[]} exclude={s.tmdb_id}
                    onCancel={() => setEditing(null)} onPick={c => rematchShow(s.id, c.tmdb_id).then(doneMatch)} />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div data-testid="tv-upgrades">
        <h3 style={sectionLabel}>Upgrades</h3>
        <UpgradesChoice value={view.upgrades.mode} onChange={mode => {
          const before = view;
          setView({ ...view, upgrades: { mode } });
          saveUpgrades(mode).then(r => { if (r.ok) setView(r.data); else { setView(before); say(r.error); } });
        }} />
      </div>
    </section>
    </WideCtx.Provider>
  );
}

// ---------------------------------------------------------------- rows

function ShowRow({ s, first, open, onOpen }: { s: Show; first: boolean; open: boolean; onOpen: () => void }) {
  const [details, setDetails] = useState(false);
  const review = needsReview(s);
  const lines = s.review || [];
  const subtitle = (
    <>
      {review && (
        <span role="img" aria-label="Needs review" data-testid="tv-review-dot"
          style={{ width: 8, height: 8, borderRadius: 4, background: T.warning, flex: 'none' }} />
      )}
      <span data-testid="tv-show-subtitle" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {showSubtitle(s)}
      </span>
      {review && lines.length > 0 && (
        <span aria-hidden="true" style={{ display: 'inline-flex', flex: 'none', transform: details ? 'rotate(90deg)' : undefined }}>
          <ChevronIcon size={12} />
        </span>
      )}
    </>
  );
  const subStyle: React.CSSProperties = { ...subText(true), display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 };
  return (
    <div style={first ? undefined : topBorder}>
      <div data-testid="tv-show" style={{ ...rowBtn, cursor: 'default' }}>
        <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={titleText(true)}>{showLabel(s)}</span>
          {review && lines.length > 0 ? (
            <button type="button" className="lp-focus" data-testid="tv-review-toggle" aria-expanded={details}
              onClick={() => setDetails(d => !d)} title={`${s.path_name} · ${s.folder}`}
              style={{ ...subStyle, background: 'none', border: 'none', padding: 0, cursor: 'pointer', textAlign: 'left', font: 'inherit', fontSize: subStyle.fontSize }}>
              {subtitle}
            </button>
          ) : (
            <span style={subStyle} title={`${s.path_name} · ${s.folder}`}>{subtitle}</span>
          )}
        </span>
        <button type="button" className="lp-focus" data-testid="tv-wrong-show" aria-expanded={open} onClick={onOpen}
          style={linkBtn}>Wrong show?</button>
      </div>
      {details && lines.length > 0 && (
        <ul data-testid="tv-review-list" style={reviewList}>
          {lines.map(l => <li key={l} style={{ listStyle: 'none' }}>{l}</li>)}
        </ul>
      )}
    </div>
  );
}

const reviewList: React.CSSProperties = {
  margin: '-4px 0 0', padding: '0 16px 12px', display: 'flex', flexDirection: 'column', gap: 4,
  fontSize: 13, lineHeight: '18px', color: T.text2,
};

function UnmatchedRow({ u, first, open, onOpen }: { u: Unmatched; first: boolean; open: boolean; onOpen: () => void }) {
  return (
    <div data-testid="tv-unmatched-row" style={{ ...rowBtn, cursor: 'default', ...(first ? null : topBorder) }}>
      <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 4, background: T.warning, flex: 'none' }} />
      <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={titleText(true)} title={u.folder}>{u.folder}</span>
        <span style={subText(true)}>{u.path_name}</span>
      </span>
      <button type="button" className="lp-focus" data-testid="tv-match" aria-expanded={open} onClick={onOpen}
        style={linkBtn}>Match</button>
    </div>
  );
}

// ---------------------------------------------------------------- fix-match picker

export function MatchPicker({ initial, year, suggestions, exclude, onPick, onCancel }: {
  initial: string; year: number | null; suggestions: Candidate[]; exclude?: number;
  onPick: (c: Candidate) => Promise<unknown>; onCancel: () => void;
}) {
  const [q, setQ] = useState(initial);
  const [results, setResults] = useState<Candidate[]>(suggestions);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const search = async (text: string, y: number | null) => {
    if (!text.trim()) return;
    setBusy(true);
    const r = await searchShows(text.trim(), y);
    setBusy(false);
    if (r.ok) { setResults(r.data.results); setError(''); } else setError(r.error);
  };
  useEffect(() => {
    if (!suggestions.length) void search(initial, year);
    // first open only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const shown = results.filter(c => c.tmdb_id !== exclude);
  return (
    <form data-testid="tv-picker" style={{ ...formBox, ...topBorder }}
      onSubmit={e => { e.preventDefault(); void search(q, null); }}>
      <Field id="tv-search" label="Show" value={q} onChange={setQ} error={error} autoFocus />
      <div style={{ display: 'flex', flexDirection: 'column', borderRadius: 12, overflow: 'hidden', background: 'rgba(255,255,255,0.03)' }}>
        {shown.map((c, i) => (
          <button key={c.tmdb_id} type="button" className="lp-row" data-testid="tv-candidate" disabled={busy}
            onClick={async () => { setBusy(true); await onPick(c); setBusy(false); }}
            style={{ ...rowBtn, minHeight: 52, ...(i === 0 ? null : topBorder) }}>
            <span style={{ ...titleText(true), flex: 1 }}>{showLabel(c)}</span>
            <span style={{ display: 'inline-flex', color: T.text4, flex: 'none' }}><ChevronIcon /></span>
          </button>
        ))}
        {!shown.length && !busy && !error && (
          <span data-testid="tv-no-results" style={{ ...rowBtn, minHeight: 52, cursor: 'default', color: 'rgba(255,255,255,0.55)' }}>No shows found</span>
        )}
      </div>
      <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
        <button type="button" onClick={onCancel} style={ghostBtn} className="lp-focus" data-testid="tv-picker-cancel">Cancel</button>
        <button type="submit" disabled={busy || !q.trim()} style={{ ...primary, opacity: busy || !q.trim() ? 0.5 : 1 }}
          className="lp-focus" data-testid="tv-search-go">Search</button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- upgrades

export const UPGRADE_OPTIONS: { id: UpgradeMode; label: string }[] = [
  { id: 'replace_verified', label: 'Replace' }, { id: 'keep_both', label: 'Keep both' },
];

function UpgradesChoice({ value, onChange }: { value: UpgradeMode; onChange: (m: UpgradeMode) => void }) {
  return (
    <div role="radiogroup" aria-label="Upgrades" style={segment}>
      {UPGRADE_OPTIONS.map(o => {
        const on = o.id === value;
        return (
          <button key={o.id} type="button" role="radio" aria-checked={on} className="lp-focus" data-testid={`tv-upgrades-${o.id}`}
            onClick={() => { if (!on) onChange(o.id); }} style={{ ...segBtn, ...(on ? segOn : null) }}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

const linkBtn: React.CSSProperties = {
  minHeight: 44, padding: '0 4px', border: 'none', background: 'none', color: T.accent, fontSize: 15, fontWeight: 500,
  fontFamily: FONT, cursor: 'pointer', flex: 'none',
};
