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
import { TodayButton, TvCalendar, WeekCenter, tvCalendarCss } from './TvCalendar';
import { TvSeries } from './TvSeries';
import { SEARCH_LIST_ID, SEARCH_MIN, TvSearch, searchRowId, tvSearchCss, type SearchKeys } from './TvSearch';
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
  const calendarTop = wide && view === 'calendar' && !noLibraries;    // the week controls sit in the top row
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

  const onWeek = (w: string) => go({ week: w === weekStart(today) ? null : w }, false);
  const search = (
    <SearchBox wide={wide} value={q} onChange={setQ}
      results={noLibraries ? noLibs : (keys, active) => (
        <TvSearch q={q} paths={settingsView.paths} onAdded={() => setReload(n => n + 1)} keys={keys} active={active} />
      )} />
  );

  let body: ReactNode;
  if (view === 'calendar') {
    body = noLibraries ? noLibs : (
      <TvCalendar today={today} week={week} reload={reload} toolbar={!calendarTop} onWeek={onWeek} />
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
    <section data-testid="tv-section" data-view={view} data-searching={searching || undefined}
      style={{ ...pad(wide), display: 'flex', flexDirection: 'column', fontFamily: FONT }}>
      <style>{libraryPathsCss}</style>
      <style>{trackersCss}</style>
      <style>{importCss}</style>
      <style>{tvCalendarCss + tvSearchCss + tabsCss}</style>
      {toast && <NoticeToast key={toast + seq.current} text={toast} onDone={() => setToast('')} />}

      {wide ? (
        // one row, 3 columns (1fr auto 1fr): the search at the far left, ‹ range › in the true center of the
        // row, Today and the view tabs on the right
        <div data-testid="tv-top-row" style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', gap: 16, minHeight: 40 }}>
          <div data-testid="tv-top-left" style={{ justifySelf: 'start', minWidth: 0 }}>{search}</div>
          <div data-testid="tv-top-center" style={{ justifySelf: 'center' }}>{calendarTop && <WeekCenter week={week} onWeek={onWeek} />}</div>
          <div data-testid="tv-top-right" style={{ justifySelf: 'end', display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
            {calendarTop && <TodayButton today={today} week={week} onWeek={onWeek} />}
            {control}
          </div>
        </div>
      ) : (
        <>
          {search}
          <div style={{ marginTop: 12 }}>{control}</div>
        </>
      )}
      {/* The Libraries view stays mounted so the selection (and the paths Add offers) is always current. */}
      <div style={{ marginTop: 20, display: view === 'libraries' ? undefined : 'none' }}>
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

export const SEARCH_W = 320;
export const SEARCH_DROP_W = 480;

/** The compact search (320px, right of the tabs) with its results in a 480px glass dropdown anchored to the
 * field's right edge. ↑/↓ move through the rows, Enter adds the highlighted show, Esc or a click outside closes. */
function SearchBox({ wide, value, onChange, results }: {
  wide: boolean; value: string; onChange: (v: string) => void;
  results: ReactNode | ((keys: React.MutableRefObject<SearchKeys | null>, active: number) => ReactNode);
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const keys = useRef<SearchKeys | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const shown = open && value.trim().length >= SEARCH_MIN;
  useEffect(() => { setActive(-1); }, [value]);
  useEffect(() => {
    if (!shown) return undefined;
    const down = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', down, true);
    return () => document.removeEventListener('pointerdown', down, true);
  }, [shown]);
  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    const n = keys.current?.count ?? 0;
    if (e.key === 'Escape') {
      if (shown) { e.preventDefault(); e.stopPropagation(); setOpen(false); return; }
      if (value) { e.preventDefault(); e.stopPropagation(); onChange(''); }
    } else if (e.key === 'ArrowDown' && n) {
      e.preventDefault(); setOpen(true); setActive(a => (a + 1) % n);
    } else if (e.key === 'ArrowUp' && n) {
      e.preventDefault(); setOpen(true); setActive(a => (a <= 0 ? n - 1 : a - 1));
    } else if (e.key === 'Enter' && shown && active >= 0) {
      e.preventDefault(); keys.current?.enter(active);
    }
  };
  useEffect(() => {
    if (active >= 0) document.getElementById(searchRowId(active))?.scrollIntoView?.({ block: 'nearest' });
  }, [active]);
  return (
    <div ref={box} data-testid="tv-search-box"
      style={{ position: 'relative', flex: wide ? `0 1 ${SEARCH_W}px` : undefined, width: wide ? SEARCH_W : '100%', minWidth: 0 }}>
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.5)" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" style={{ position: 'absolute', left: 12, top: 10, pointerEvents: 'none' }}>
        <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
      </svg>
      <input type="search" data-testid="tv-search" aria-label="Search shows" placeholder="Search shows" value={value}
        className="tvt-search" role="combobox" aria-expanded={shown} aria-controls={SEARCH_LIST_ID} aria-autocomplete="list"
        aria-activedescendant={shown && active >= 0 ? searchRowId(active) : undefined}
        onChange={e => { onChange(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        style={{
          width: '100%', height: 36, boxSizing: 'border-box', padding: '0 36px 0 36px', borderRadius: 10, outline: 'none',
          background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', color: '#fff', fontSize: 14,
          fontFamily: FONT, colorScheme: 'dark',
        }} />
      {value && (
        <button type="button" aria-label="Clear" data-testid="tv-search-clear" className="lp-focus" onClick={() => { onChange(''); setOpen(false); }}
          style={{ position: 'absolute', right: 0, top: 0, width: 36, height: 36, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            border: 'none', background: 'none', color: 'rgba(255,255,255,0.6)', cursor: 'pointer', borderRadius: 10 }}>
          <XIcon size={14} />
        </button>
      )}
      {shown && (
        <div data-testid="tv-search-drop"
          style={{ position: 'absolute', zIndex: 60, top: 'calc(100% + 6px)', left: 0, width: SEARCH_DROP_W, maxWidth: 'calc(100vw - 32px)',
            maxHeight: 'min(70vh, 560px)', overflowY: 'auto', boxSizing: 'border-box', borderRadius: 12,
            background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
            backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)', boxShadow: '0 16px 48px rgba(0,0,0,0.45)' }}>
          {typeof results === 'function' ? results(keys, active) : results}
        </div>
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
