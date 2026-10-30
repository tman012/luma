/** Settings > TV (owner only, luma-test builds), docs/design/tv.md: the "TV"
 * title row with Series | Calendar | Import | Libraries | Options (in the URL as ?view=,
 * so Back works), Search under it, and the view. Calendar (the default) shows
 * the week's episodes by air date; Search finds library shows and TMDB shows
 * (Add); Import is the Import card, Review and the shows; Libraries is the TV
 * libraries selector; Options holds Upgrades, Search (v256) and Trackers. Importing only reads
 * the library folders and Add only writes tv.db. */
import { useCallback, useEffect, useRef, useState, type ReactNode, type CSSProperties } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
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
import { QualityProfiles } from './TvEdit';
import { SEARCH_LIST_ID, SEARCH_MIN, TV_POPOVER_SURFACE, TvOwnedSearch, searchRowId, tvSearchCss, type SearchKeys } from './TvSearch';
import { TvSeriesDetail } from './TvSeriesDetail';
import { NamingSettings } from './TvNaming';
import { AddSeriesButton } from './TvAddSeries';
import { OpenShowContext, usePhone } from './TvCalendar';
import { TvEpisodePage } from './TvEpisodePage';
import { LibrarySelector } from './LibrarySelector';
import type { SelectableLibrary } from './librarySelectionApi';
import { SAVE_FAILED } from './libraryPathsApi';
import type { Candidate } from './tvLibraryApi';
import {
  TV_LIBRARIES_URL, importAll, importedText, showSubtitle, loadTv, loadTvLibrary, needsReview, rematchShow,
  saveAutomation, saveUpgrades, savePreferredGroups, showLabel, startImport,
  type ImportProgress, type Show, type TvLibrary, type TvSettingsView, type UpgradeMode,
} from './tvLibraryApi';
import { isDay, localDay, weekStart } from './tvCalendarApi';
import { TvSearchSettings } from './SearchSettings';
import { TV_SETTINGS_PATH } from './tvSettingsRoutes';

/** Space between sections (Import, Review, Shows; Upgrades, Trackers), as on Library paths. */
export const SECTION_GAP = 28;
export const TV_VIEWS = [
  { id: 'series', label: 'Series' }, { id: 'calendar', label: 'Calendar' }, { id: 'import', label: 'Import' },
] as const;
export { TV_SETTINGS_PATH, TV_SETTINGS_VIEWS } from './tvSettingsRoutes';
export type TvView = typeof TV_VIEWS[number]['id'];
export const tvView = (v: string | null): TvView => (TV_VIEWS.find(x => x.id === v)?.id ?? 'calendar');

/** mode 'views': Series | Calendar | Import (Home > Calendar). mode 'settings': Settings > TV, Library options then Options. */
export function TvLibrarySettings({ wide, options, mode = 'views' }: { wide: boolean; options?: ReactNode; mode?: 'views' | 'settings' }) {
  const navigate = useNavigate();
  const settingsMode = mode === 'settings';
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
  const [heroEl, setHeroEl] = useState<HTMLDivElement | null>(null);
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
  // v181: a Series edit (bulk, episode select, Edit, Add) reloads the shows as well as the calendar
  const changed = useCallback(() => { void refreshLib(); setReload(n => n + 1); }, [refreshLib]);
  const onFinish = useCallback((p: ImportProgress) => {
    setImportError(p.error);
    setReload(n => n + 1);
    void refreshLib().then(() => setImporting(false));
  }, [refreshLib]);

  const phone = usePhone();   // v201: before the early return (hooks order)
  if (!settingsView) {
    return loadFailed ? <div role="alert" style={{ ...pad(wide), ...muted }}>{"Couldn't load TV settings"}</div> : null;
  }
  const say = (msg: string) => { seq.current += 1; setToast(msg); };
  const go = (patch: Record<string, string | null>, push = true) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) { if (v === null) next.delete(k); else next.set(k, v); }
    setParams(next, { replace: !push });
  };
  // v184 (PM): a top tab always opens its root (never a remembered sub-page such as a show's detail);
  // pushed, so browser Back still returns to where you were
  const pickView = (v: TvView) => { setQ(''); go({ view: v === 'calendar' ? null : v, show: null, episode: null }); };
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
  // v326 (Taylor): the desktop calendar has no search; its This week strip takes the search's place in the top row.
  const deskCalendar = wide && view === 'calendar';
  const control = (
    phone ? <PhoneSegmented value={view} onPick={pickView} /> : <Segmented value={view} wide={wide} onPick={pickView} />
  );
  const noLibs = (
    <div data-testid="tv-no-libraries" style={{ minHeight: 240, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
      <span style={{ fontSize: 15, fontWeight: 500, color: 'rgba(255,255,255,0.95)' }}>No TV libraries</span>
      <button type="button" className="lp-focus" data-testid="tv-choose-libraries" onClick={() => { setQ(''); navigate(TV_SETTINGS_PATH); }} style={{ ...primary, cursor: 'pointer' }}>
        Choose libraries
      </button>
    </div>
  );

  const onWeek = (w: string) => go({ week: w === weekStart(today) ? null : w }, false);
  // v182: the series detail page (?view=series&show=<id>), from Series, the calendar and search results
  const showId = Number(params.get('show')) || 0;
  const openDetail = (id: number, episodeId?: number | null) => {
    setQ('');
    go({ view: 'series', show: String(id), episode: episodeId ? String(episodeId) : null });
  };
  const openShow = (s: Show) => openDetail(s.id);
  // v321 (Taylor via PM): the series detail page has no "Search your shows" box
  const detailRoute = view === 'series' && !!showId && !noLibraries;
  const search = (
    // v182 (Taylor): on every TV view the toolbar finds only your own shows (tv.db, already loaded): no network
    // search and no Add; a result opens the detail page. Adding is the Series page's own Add series flow.
    <SearchBox wide={wide} value={q} onChange={setQ} placeholder="Search your shows"
      results={noLibraries ? noLibs : (keys, active) => (
        <TvOwnedSearch q={q} shows={lib ? lib.shows : null} onOpen={openShow} keys={keys} active={active} />
      )} />
  );

  const optionsBody = (
      <div style={{ display: 'flex', flexDirection: 'column', gap: SECTION_GAP }}>
        <div data-testid="tv-upgrades">
          <h3 style={sectionLabel}>Upgrades</h3>
          <UpgradesChoice value={settingsView.upgrades.mode} onChange={mode => {
            const before = settingsView;
            setSettingsView({ ...settingsView, upgrades: { mode } });
            saveUpgrades(mode).then(r => { if (r.ok) setSettingsView(r.data); else { setSettingsView(before); say(r.error); } });
          }} />
        </div>
        <QualityProfiles />
        <PreferredGroups value={settingsView.preferred_groups ?? []} onChange={g => {
          const before = settingsView;
          setSettingsView({ ...settingsView, preferred_groups: g });
          savePreferredGroups(g).then(r => { if (r.ok) setSettingsView(r.data); else { setSettingsView(before); say(r.error); } });
        }} />
        {settingsView.naming && (
          <NamingSettings value={settingsView.naming} say={say}
            onSaved={n => setSettingsView(v => (v ? { ...v, naming: n } : v))} />
        )}
        <TvSearchSettings value={settingsView.automation ?? {}} onChange={patch => {
          const before = settingsView;
          setSettingsView({ ...settingsView, automation: { ...settingsView.automation, ...patch } });
          saveAutomation(patch).then(r => { if (r.ok) setSettingsView(r.data); else { setSettingsView(before); say(r.error); } });
        }} />
        {options}
      </div>
  );
  if (settingsMode) {
    return (
      <WideCtx.Provider value={wide}>
      <section data-testid="tv-settings" style={{ ...pad(wide), display: 'flex', flexDirection: 'column', gap: SECTION_GAP, fontFamily: FONT }}>
        <style>{libraryPathsCss}</style>
        <style>{trackersCss}</style>
        {toast && <NoticeToast key={toast + seq.current} text={toast} onDone={() => setToast('')} />}
        <div data-testid="tv-library-options">
          <LibrarySelector url={TV_LIBRARIES_URL} label="Library options" testId="tv-paths" onChange={onLibraries} />
        </div>
        <div data-testid="tv-options">{optionsBody}</div>
      </section>
      </WideCtx.Provider>
    );
  }

  let body: ReactNode;
  if (view === 'calendar') {
    body = noLibraries ? noLibs : (
      <OpenShowContext.Provider value={openDetail}>
        <TvCalendar today={today} week={week} reload={reload} toolbar={!calendarTop} onWeek={onWeek} heroIn={calendarTop ? heroEl : null} />
      </OpenShowContext.Provider>
    );
  } else if (view === 'series') {
    const epId = Number(params.get('episode')) || 0;
    body = noLibraries ? noLibs : showId && epId && phone ? (
      // v201 (Taylor's pick E2): on a phone an episode opens its own page
      <TvEpisodePage key={`${showId}/${epId}`} seriesId={showId} episodeId={epId} onChanged={changed}
        onBack={() => { if (window.history.length > 1) window.history.back(); else go({ episode: null }); }} />
    ) : showId ? (
      <TvSeriesDetail key={showId} seriesId={showId} episodeId={Number(params.get('episode')) || null} wide={wide}
        onBack={() => go({ show: null, episode: null })} onChanged={changed} say={say} />
    ) : <TvSeries shows={lib ? lib.shows : null} onChanged={changed} onOpen={s => openDetail(s.id)}
      add={<AddSeriesButton paths={settingsView.paths} onAdded={changed} />} />;
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
  } else {
    body = null;
  }

  return (
    <WideCtx.Provider value={wide}>
    <section data-testid="tv-section" data-view={view} data-searching={searching || undefined}
      style={{ ...pad(wide), display: 'flex', flexDirection: 'column', fontFamily: FONT, containerType: 'inline-size', containerName: 'tvtop' }}>
      <style>{libraryPathsCss}</style>
      <style>{trackersCss}</style>
      <style>{importCss}</style>
      <style>{tvCalendarCss + tvSearchCss + tabsCss}</style>
      {toast && <NoticeToast key={toast + seq.current} text={toast} onDone={() => setToast('')} />}

      {wide ? (
        // v158c (Designer): a 3-column grid, minmax(200px, 320px) 1fr auto: search left, the week controls
        // centered in the middle column, the tabs right. Under 1000px of content (container query): the tabs
        // on their own row above, right-aligned, 12px gap; search and the week controls on the row below.
        <div data-testid="tv-top-row" className="tvt-top" data-cal={deskCalendar || undefined}>
          <div data-testid="tv-top-left" className="tvt-top-search" ref={calendarTop ? setHeroEl : undefined}>{deskCalendar || detailRoute ? null : search}</div>
          <div data-testid="tv-top-center" className="tvt-top-week">
            {calendarTop && <WeekCenter week={week} onWeek={onWeek} />}
            {calendarTop && <TodayButton today={today} week={week} onWeek={onWeek} />}
          </div>
          <div data-testid="tv-top-right" className="tvt-top-tabs">{control}</div>
        </div>
      ) : (
        <>
          {!detailRoute && search}
          <div style={{ marginTop: detailRoute ? 0 : 12 }}>{control}</div>
        </>
      )}
      {body && <div data-testid="tv-view" style={{ marginTop: 20 }}>{body}</div>}
    </section>
    </WideCtx.Provider>
  );
}

// ---------------------------------------------------------------- frame

/** v226 (Taylor's S1) the phone tab icons, 18px line icons as the mock. */
const PHONE_TAB_ICONS: Record<TvView, ReactNode> = {
  series: <><rect x="3" y="5" width="18" height="12" rx="2" /><path d="M8 21h8" /></>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" /></>,
  import: <path d="M12 3v12M7 10l5 5 5-5M4 21h16" />,
};

/** v226 (Taylor's S1): on a phone the views are a full-width 3-part segmented control, an icon over each
 * label, equal parts that fit 320 to 430px with nothing cut off (no horizontal slider). */
function PhoneSegmented({ value, onPick }: { value: TvView; onPick: (v: TvView) => void }) {
  return (
    <div role="tablist" aria-label="TV" data-testid="tv-tabs" data-variant="phone"
      style={{ display: 'flex', width: '100%', height: 56, padding: 4, gap: 0, boxSizing: 'border-box', borderRadius: 14,
        background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)' }}>
      {TV_VIEWS.map(t => {
        const on = t.id === value;
        return (
          <button key={t.id} type="button" role="tab" aria-selected={on} className="lp-focus" data-testid={`tv-tab-${t.id}`}
            onClick={() => onPick(t.id)}
            style={{ flex: '1 1 0', minWidth: 0, padding: 0, border: 'none', borderRadius: 10, cursor: 'pointer', fontFamily: FONT,
              display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 2,
              fontSize: 12, fontWeight: 600, lineHeight: '14px', whiteSpace: 'nowrap',
              background: on ? '#3b82f6' : 'transparent', color: on ? '#fff' : 'rgba(255,255,255,0.7)' }}>
            <svg data-testid="tv-tab-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{PHONE_TAB_ICONS[t.id]}</svg>
            <span>{t.label}</span>
          </button>
        );
      })}
    </div>
  );
}

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
function SearchBox({ wide, value, onChange, results, placeholder = 'Search shows' }: {
  wide: boolean; value: string; onChange: (v: string) => void; placeholder?: string;
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
      style={{ position: 'relative', width: '100%', maxWidth: wide ? SEARCH_W : undefined, minWidth: 0 }}>
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.5)" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" style={{ position: 'absolute', left: 12, top: 10, pointerEvents: 'none' }}>
        <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
      </svg>
      <input type="search" data-testid="tv-search" aria-label={placeholder} placeholder={placeholder} value={value}
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
            ...TV_POPOVER_SURFACE, boxShadow: '0 16px 48px rgba(0,0,0,0.45)' }}>
          {typeof results === 'function' ? results(keys, active) : results}
        </div>
      )}
    </div>
  );
}

/** The top row (v158c, Designer). Exported for tests. */
/** MOTION.md §4 (v325): the desktop Calendar navigator sits 12px under the page title. The title is centered on the
 * page header (LibrariesPage: 20px top + 64px box, side padding 44 left / 60 right) and this section starts
 * HEADING_GAP (16px) under the header, so the row moves up 4px and right (60 - 44) / 2 = 8px to share its axis. */
export const CAL_NAV_TOP = 12 - 16;
export const CAL_NAV_SHIFT = (60 - 44) / 2;
export const TOP_COLUMNS = 'minmax(200px, 320px) 1fr auto';
export const topRowCss = `
.tvt-top{display:grid;grid-template-columns:${TOP_COLUMNS};grid-template-areas:"search week tabs";align-items:center;column-gap:16px;row-gap:12px;min-height:40px}
.tvt-top-search{grid-area:search;min-width:0}
.tvt-top-week{grid-area:week;justify-self:center;display:flex;align-items:center;gap:12px;min-width:0}
.tvt-top-tabs{grid-area:tabs;justify-self:end;min-width:0}
.tvt-top[data-cal]{grid-template-columns:1fr auto 1fr;grid-template-areas:"week week week" "search . tabs";row-gap:20px}
.tvt-top[data-cal] .tvt-top-week{position:relative;justify-self:center;margin-top:${CAL_NAV_TOP}px;left:${CAL_NAV_SHIFT}px}
.tvt-top[data-cal] [data-testid="tv-cal-center"]{min-width:420px;justify-content:space-between}
.tvt-top[data-cal] .tvt-top-week [data-testid="tv-cal-today"]{position:absolute;left:calc(100% + 12px);top:50%;transform:translateY(-50%);margin-left:0}
@container tvtop (max-width: 999px){.tvt-top[data-cal]{grid-template-columns:minmax(200px, 320px) 1fr;grid-template-areas:"tabs tabs" "search week";row-gap:12px}
.tvt-top[data-cal] .tvt-top-week{margin-top:0;left:0}.tvt-top[data-cal] [data-testid="tv-cal-center"]{min-width:0}
.tvt-top[data-cal] .tvt-top-week [data-testid="tv-cal-today"]{position:static;transform:none}}
@container tvtop (max-width: 999px){.tvt-top{grid-template-columns:minmax(200px, 320px) 1fr;grid-template-areas:"tabs tabs" "search week"}}
`;
const tabsCss = topRowCss + `
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

/** v185.2a (Taylor): preferred release groups, in order; a release from one wins at the same quality.
 * Starts empty; add, remove and reorder. */
/** card: Settings > Movies draws the groups as glass-card rows (56px, 16px padding) like its Trackers card; TV keeps the bare list. */
export function PreferredGroups({ value, onChange, card = false }: { value: string[]; onChange: (g: string[]) => void; card?: boolean }) {
  const [draft, setDraft] = useState('');
  const btn: CSSProperties = { minHeight: 32, padding: '0 10px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.14)',
    background: 'rgba(255,255,255,0.06)', color: '#fff', fontSize: 13, cursor: 'pointer' };
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= value.length) return;
    const g = [...value];
    [g[i], g[j]] = [g[j]!, g[i]!];
    onChange(g);
  };
  const add = () => {
    const v = draft.trim();
    if (!v || value.some(x => x.toLowerCase() === v.toLowerCase())) { setDraft(''); return; }
    onChange([...value, v]);
    setDraft('');
  };
  return (
    <div data-testid="tv-preferred-groups">
      <h3 style={sectionLabel}>Preferred groups</h3>
      {card ? (
        <div data-testid="tv-groups-card" style={GROUP_CARD}>
          <ol style={card ? { listStyle: 'none', margin: 0, padding: 0 } : { listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {value.map((g, i) => (
              <li key={g} data-testid="tv-group" style={card ? { ...groupRow, borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none' } : { display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, color: '#fff' }}>
                <span style={{ minWidth: 20, color: 'rgba(255,255,255,0.5)', fontSize: 13 }}>{i + 1}</span>
                <span style={{ flex: 1 }}>{g}</span>
                <button type="button" style={btn} aria-label={`Move ${g} up`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                <button type="button" style={btn} aria-label={`Move ${g} down`} disabled={i === value.length - 1} onClick={() => move(i, 1)}>↓</button>
                <button type="button" style={btn} aria-label={`Remove ${g}`} onClick={() => onChange(value.filter(x => x !== g))}>Remove</button>
              </li>
            ))}
          </ol>
          <div data-testid="tv-group-add-row" style={{ ...groupRow, borderTop: value.length ? '1px solid rgba(255,255,255,0.06)' : 'none', gap: 8 }}>
            <input value={draft} onChange={e => setDraft(e.target.value)} maxLength={40} placeholder="Group" aria-label="Group"
              onKeyDown={e => { if (e.key === 'Enter') add(); }}
              style={{ flex: '0 1 220px', minHeight: 36, padding: '0 10px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.14)',
                background: 'rgba(255,255,255,0.04)', color: '#fff', fontSize: 14 }} />
            <button type="button" style={btn} onClick={add} disabled={!draft.trim()}>Add</button>
          </div>
        </div>
      ) : (<>
      <ol style={card ? { listStyle: 'none', margin: 0, padding: 0, ...GROUP_CARD } : { listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {value.map((g, i) => (
          <li key={g} data-testid="tv-group" style={card ? { ...groupRow, borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none' } : { display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, color: '#fff' }}>
            <span style={{ minWidth: 20, color: 'rgba(255,255,255,0.5)', fontSize: 13 }}>{i + 1}</span>
            <span style={{ flex: 1 }}>{g}</span>
            <button type="button" style={btn} aria-label={`Move ${g} up`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
            <button type="button" style={btn} aria-label={`Move ${g} down`} disabled={i === value.length - 1} onClick={() => move(i, 1)}>↓</button>
            <button type="button" style={btn} aria-label={`Remove ${g}`} onClick={() => onChange(value.filter(x => x !== g))}>Remove</button>
          </li>
        ))}
      </ol>
      <div style={card ? { display: 'flex', gap: 8, marginTop: 10 } : { display: 'flex', gap: 8, marginTop: value.length ? 10 : 0 }}>
        <input value={draft} onChange={e => setDraft(e.target.value)} maxLength={40} placeholder="Group" aria-label="Group"
          onKeyDown={e => { if (e.key === 'Enter') add(); }}
          style={{ flex: '0 1 220px', minHeight: 36, padding: '0 10px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.14)',
            background: 'rgba(255,255,255,0.04)', color: '#fff', fontSize: 14 }} />
        <button type="button" style={btn} onClick={add} disabled={!draft.trim()}>Add</button>
      </div>
      </>)}
    </div>
  );
}

const GROUP_CARD: CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' };
const groupRow: CSSProperties = { minHeight: 56, display: 'flex', alignItems: 'center', gap: 10, padding: '0 16px', fontSize: 14, color: '#fff' };
