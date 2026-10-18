/** Settings > TV (owner only, luma-test builds), docs/design/tv.md: the "TV"
 * title row with Series | Calendar | Import | Libraries | Options (in the URL as ?view=,
 * so Back works), Search under it, and the view. Calendar (the default) shows
 * the week's episodes by air date; Search finds library shows and TMDB shows
 * (Add); Import is the Import card, Review and the shows; Libraries is the TV
 * libraries selector; Options holds Upgrades and Trackers. Importing only reads
 * the library folders and Add only writes tv.db. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import { T } from '@/theme/tokens';
import { FONT } from '@/components/start/StartUI';
import { NoticeToast } from '@/components/start/SetupToast';
import { ChevronIcon, XIcon } from '@/components/nav/AppNav';
import { trackersCss } from '@/components/trackers/trackersUi';
import {
  WideCtx, card, libraryPathsCss, muted, pad, primary, rowBtn, sectionLabel, segBtn, segOn, segment, subText, titleText, topBorder,
} from './LibraryPathsSettings';
import { ImportSection, ReviewSection, ShowPicker, FADE_MS, importCss, initialReview, type ReviewState } from './TvImport';
import { TvCalendar, tvCalendarCss } from './TvCalendar';
import { TvSeries } from './TvSeries';
import { SEARCH_MIN, TvSearch, tvSearchCss } from './TvSearch';
import { LibrarySelector } from './LibrarySelector';
import type { SelectableLibrary } from './librarySelectionApi';
import { SAVE_FAILED } from './libraryPathsApi';
import type { Candidate } from './tvLibraryApi';
import {
  TV_LIBRARIES_URL, importAll, importedText, showSubtitle, loadTv, loadTvLibrary, needsReview, rematchShow,
  saveUpgrades, showLabel, startImport,
  type ImportProgress, type Show, type TvLibrary, type TvSettingsView, type UpgradeMode,
} from './tvLibraryApi';
import { isDay, localDay, weekStart } from './tvCalendarApi';

/** Space between sections (Import, Review, Shows; Upgrades, Trackers), as on Library paths. */
export const SECTION_GAP = 28;
export const TV_VIEWS = [
  { id: 'series', label: 'Series' }, { id: 'calendar', label: 'Calendar' }, { id: 'import', label: 'Import' },
  { id: 'libraries', label: 'Libraries' }, { id: 'options', label: 'Options' },
] as const;
export type TvView = typeof TV_VIEWS[number]['id'];
export const tvView = (v: string | null): TvView => (TV_VIEWS.find(x => x.id === v)?.id ?? 'calendar');

export function TvLibrarySettings({ wide, options }: { wide: boolean; options?: ReactNode }) {
  const [settingsView, setSettingsView] = useState<TvSettingsView | null>(null);
  const [lib, setLib] = useState<TvLibrary | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [toast, setToast] = useState('');
  const [editing, setEditing] = useState<number | null>(null);     // the show whose "Wrong show?" picker is open
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [review, setReview] = useState<ReviewState>({ checked: {}, picks: {} });
  const [importingAll, setImportingAll] = useState(false);
  const [fading, setFading] = useState<Set<number>>(new Set());
  const [q, setQ] = useState('');
  const [reload, setReload] = useState(0);                          // the Calendar reloads after an import or an Add
  const seq = useRef(0);
  const [params, setParams] = useSearchParams();
  const view = tvView(params.get('view'));
  const today = localDay();
  const week = isDay(params.get('week')) ? weekStart(params.get('week')!) : weekStart(today);

  const takeLib = useCallback((data: TvLibrary) => {
    setLib(data);
    setReview(prev => initialReview(data.unmatched, prev));
  }, []);
  const refreshLib = useCallback(() => loadTvLibrary().then(r => {
    if (r.ok) {
      takeLib(r.data);
      if (r.data.scan.running) setImporting(true);
    }
    return r;
  }), [takeLib]);
  const picked = useRef<string | null>(null);
  const onLibraries = useCallback((sel: SelectableLibrary[]) => {
    const key = sel.map(l => l.id).join(',');
    setSettingsView(v => (v ? { ...v, paths: sel.map(({ selected: _s, ...p }) => p) } : v));
    if (picked.current !== null && picked.current !== key) { void refreshLib(); setReload(n => n + 1); }   // shows follow the selection
    picked.current = key;
  }, [refreshLib]);
  useEffect(() => {
    loadTv().then(r => (r.ok ? setSettingsView(r.data) : setLoadFailed(true)));
    refreshLib();
  }, [refreshLib]);
  const onFinish = useCallback((p: ImportProgress) => {
    setImportError(p.error);
    setReload(n => n + 1);
    void refreshLib().then(() => setImporting(false));
  }, [refreshLib]);

  if (!settingsView) {
    return loadFailed ? <div role="alert" style={{ ...pad(wide), ...muted }}>{"Couldn't load TV settings"}</div> : null;
  }
  const say = (msg: string) => { seq.current += 1; setToast(msg); };
  const go = (patch: Record<string, string | null>, push = true) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) { if (v === null) next.delete(k); else next.set(k, v); }
    setParams(next, { replace: !push });
  };
  const pickView = (v: TvView) => { setQ(''); go({ view: v === 'calendar' ? null : v }); };
  const start = () => {
    setImportError(null);
    setImporting(true);
    void startImport().then(r => { if (!r.ok) { setImporting(false); setImportError(r.error); } });
  };
  const runImportAll = async () => {
    if (!lib) return;
    const items = lib.unmatched.filter(u => review.checked[u.id] && review.picks[u.id])
      .map(u => ({ id: u.id, tmdb_id: review.picks[u.id]!.tmdb_id }));
    if (!items.length) return;
    setImportingAll(true);
    const r = await importAll(items);
    if (!r.ok) { setImportingAll(false); say(r.error); return; }
    const done = new Set(r.data.imported);
    setFading(done);                                     // imported rows fade out, then Shows updates in place
    await new Promise(res => window.setTimeout(res, FADE_MS));
    takeLib(r.data);
    setFading(new Set());
    setImportingAll(false);
    setReload(n => n + 1);
    say(r.data.imported.length ? importedText(r.data.imported.length) : (r.data.failed[0]?.error || SAVE_FAILED));
  };
  const reviewRows = lib?.unmatched || [];
  const noLibraries = settingsView.paths.length === 0;
  const searching = q.trim().length >= SEARCH_MIN;
  const control = (
    <Segmented value={view} wide={wide} onPick={pickView} />
  );
  const noLibs = (
    <div data-testid="tv-no-libraries" style={{ minHeight: 240, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
      <span style={{ fontSize: 15, fontWeight: 500, color: 'rgba(255,255,255,0.95)' }}>No TV libraries</span>
      <button type="button" className="lp-focus" data-testid="tv-choose-libraries" onClick={() => pickView('libraries')} style={{ ...primary, cursor: 'pointer' }}>
        Choose libraries
      </button>
    </div>
  );

  let body: ReactNode;
  if (searching) {
    body = noLibraries ? noLibs : (
      <TvSearch q={q} paths={settingsView.paths} onAdded={() => setReload(n => n + 1)} />
    );
  } else if (view === 'calendar') {
    body = noLibraries ? noLibs : (
      <TvCalendar today={today} week={week} reload={reload}
        onWeek={w => go({ week: w === weekStart(today) ? null : w }, false)} />
    );
  } else if (view === 'series') {
    body = noLibraries ? noLibs : <TvSeries shows={lib ? lib.shows : null} />;
  } else if (view === 'import') {
    body = (
      <div style={{ display: 'flex', flexDirection: 'column', gap: SECTION_GAP }}>
        {noLibraries ? noLibs : (
          <ImportSection wide={wide} running={importing} error={importError} lastImport={lib?.scan.last_scan ?? null}
            onStart={start} onFinish={onFinish} />
        )}
        {!importing && reviewRows.length > 0 && (
          <ReviewSection wide={wide} rows={reviewRows} state={review} setState={setReview} busy={importingAll}
            fading={fading} onImportAll={() => void runImportAll()} />
        )}
        {lib && lib.shows.length > 0 && (
          <div data-testid="tv-shows">
            <h3 style={sectionLabel}>Shows</h3>
            <div style={{ ...card, overflow: 'visible' }}>
              {lib.shows.map((s, i) => (
                <ShowRow key={s.id} s={s} first={i === 0} wide={wide} open={editing === s.id}
                  onOpen={() => setEditing(editing === s.id ? null : s.id)} onClose={() => setEditing(null)}
                  onPick={c => {
                    if (c.tmdb_id === s.tmdb_id) { setEditing(null); return; }
                    void rematchShow(s.id, c.tmdb_id).then(r => {
                      if (r.ok) { takeLib(r.data); setEditing(null); setReload(n => n + 1); say('Matched'); } else say(r.error || SAVE_FAILED);
                    });
                  }} />
              ))}
            </div>
          </div>
        )}
      </div>
    );
  } else if (view === 'options') {
    body = (
      <div style={{ display: 'flex', flexDirection: 'column', gap: SECTION_GAP }}>
        <div data-testid="tv-upgrades">
          <h3 style={sectionLabel}>Upgrades</h3>
          <UpgradesChoice value={settingsView.upgrades.mode} onChange={mode => {
            const before = settingsView;
            setSettingsView({ ...settingsView, upgrades: { mode } });
            saveUpgrades(mode).then(r => { if (r.ok) setSettingsView(r.data); else { setSettingsView(before); say(r.error); } });
          }} />
        </div>
        {options}
      </div>
    );
  } else {
    body = null;
  }

  return (
    <WideCtx.Provider value={wide}>
    <section data-testid="tv-section" data-view={searching ? 'search' : view}
      style={{ ...pad(wide), display: 'flex', flexDirection: 'column', fontFamily: FONT }}>
      <style>{libraryPathsCss}</style>
      <style>{trackersCss}</style>
      <style>{importCss}</style>
      <style>{tvCalendarCss + tvSearchCss + tabsCss}</style>
      {toast && <NoticeToast key={toast + seq.current} text={toast} onDone={() => setToast('')} />}

      {wide && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, minHeight: 40 }}>
          <h2 data-testid="tv-title" style={{ margin: 0, flex: 1, fontSize: 18, fontWeight: 600, color: 'rgba(255,255,255,0.95)' }}>TV</h2>
          {control}
        </div>
      )}
      <SearchField wide={wide} value={q} onChange={setQ} />
      {!wide && <div style={{ marginTop: 12 }}>{control}</div>}
      {/* The Libraries view stays mounted so the selection (and the paths Add offers) is always current. */}
      <div style={{ marginTop: 20, display: view === 'libraries' && !searching ? undefined : 'none' }}>
        <LibrarySelector url={TV_LIBRARIES_URL} label="Libraries" testId="tv-paths" onChange={onLibraries} />
      </div>
      {body && <div data-testid="tv-view" style={{ marginTop: 20 }}>{body}</div>}
    </section>
    </WideCtx.Provider>
  );
}

// ---------------------------------------------------------------- frame

function Segmented({ value, wide, onPick }: { value: TvView; wide: boolean; onPick: (v: TvView) => void }) {
  return (
    <div className="tvt-scroll" style={{ overflowX: wide ? 'visible' : 'auto' }}>
      <div role="tablist" aria-label="TV" data-testid="tv-tabs"
        style={{ ...segment, maxWidth: 'none', width: wide ? 'auto' : '100%', minWidth: wide ? undefined : 'max-content', boxSizing: 'border-box' }}>
        {TV_VIEWS.map(t => {
          const on = t.id === value;
          return (
            <button key={t.id} type="button" role="tab" aria-selected={on} className="lp-focus" data-testid={`tv-tab-${t.id}`}
              onClick={() => onPick(t.id)}
              style={{ ...segBtn, ...(on ? segOn : null), height: wide ? 32 : 38, padding: '0 14px', flex: wide ? 'none' : 1,
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, whiteSpace: 'nowrap' }}>
              {t.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function SearchField({ wide, value, onChange }: { wide: boolean; value: string; onChange: (v: string) => void }) {
  return (
    <div style={{ position: 'relative', marginTop: wide ? 12 : 0 }}>
      <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.5)" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" style={{ position: 'absolute', left: 14, top: 13, pointerEvents: 'none' }}>
        <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
      </svg>
      <input type="search" data-testid="tv-search" aria-label="Search shows" placeholder="Search shows" value={value}
        className="tvt-search"
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => { if (e.key === 'Escape' && value) { e.preventDefault(); e.stopPropagation(); onChange(''); } }}
        style={{
          width: '100%', height: 44, boxSizing: 'border-box', padding: '0 44px 0 42px', borderRadius: 12, outline: 'none',
          background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', color: '#fff', fontSize: 15,
          fontFamily: FONT, colorScheme: 'dark',
        }} />
      {value && (
        <button type="button" aria-label="Clear" data-testid="tv-search-clear" className="lp-focus" onClick={() => onChange('')}
          style={{ position: 'absolute', right: 0, top: 0, width: 44, height: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            border: 'none', background: 'none', color: 'rgba(255,255,255,0.6)', cursor: 'pointer', borderRadius: 12 }}>
          <XIcon size={16} />
        </button>
      )}
    </div>
  );
}

const tabsCss = `
.tvt-scroll{scrollbar-width:none}.tvt-scroll::-webkit-scrollbar{display:none}
.tvt-search::-webkit-search-cancel-button{display:none}
.tvt-search:focus{border-color:#3b82f6!important;box-shadow:0 0 0 3px rgba(59,130,246,.25)}
.tvt-search::placeholder{color:rgba(255,255,255,.4)}
`;

// ---------------------------------------------------------------- rows

function ShowRow({ s, first, wide, open, onOpen, onClose, onPick }: {
  s: Show; first: boolean; wide: boolean; open: boolean; onOpen: () => void; onClose: () => void; onPick: (c: Candidate) => void;
}) {
  const [details, setDetails] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const review = needsReview(s);
  const lines = s.review || [];
  const subtitle = (
    <>
      {review && (
        <span role="img" aria-label="Needs review" data-testid="tv-review-dot"
          style={{ width: 8, height: 8, borderRadius: 4, background: T.warning, flex: 'none' }} />
      )}
      <span data-testid="tv-show-subtitle" style={{ minWidth: 0, whiteSpace: 'normal', overflowWrap: 'anywhere' }}>
        {showSubtitle(s)}
      </span>
      {review && lines.length > 0 && (
        <span aria-hidden="true" style={{ display: 'inline-flex', flex: 'none', transform: details ? 'rotate(90deg)' : undefined }}>
          <ChevronIcon size={12} />
        </span>
      )}
    </>
  );
  // Wraps at 390 instead of clipping (Tester M23); the dot and chevron stay beside it.
  const subStyle: React.CSSProperties = {
    ...subText(true), display: 'flex', alignItems: 'center', gap: 6, minWidth: 0,
    whiteSpace: 'normal', overflow: 'visible', textOverflow: 'clip',
  };
  return (
    <div style={{ position: 'relative', ...(first ? null : topBorder) }}>
      <div data-testid="tv-show" style={{ ...rowBtn, cursor: 'default' }}>
        <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={titleText(true)}>{showLabel(s)}</span>
          {review && lines.length > 0 ? (
            <button type="button" className="lp-focus" data-testid="tv-review-toggle" aria-expanded={details}
              onClick={() => setDetails(d => !d)} title={`${s.path_name} · ${s.folder}`}
              style={{ ...subStyle, background: 'none', border: 'none', padding: 0, cursor: 'pointer', textAlign: 'left', fontFamily: 'inherit' }}>
              {subtitle}
            </button>
          ) : (
            <span style={subStyle} title={`${s.path_name} · ${s.folder}`}>{subtitle}</span>
          )}
        </span>
        <button ref={anchor} type="button" className="lp-focus" data-testid="tv-wrong-show" aria-haspopup="dialog" aria-expanded={open}
          onClick={onOpen} style={linkBtn}>Wrong show?</button>
      </div>
      {open && (
        <ShowPicker wide={wide} anchor={anchor} initial={s.title} year={null} current={s.tmdb_id}
          suggestions={[]} onPick={onPick} onClose={onClose} />
      )}
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
