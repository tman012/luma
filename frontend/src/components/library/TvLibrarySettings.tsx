/** Settings > TV (owner only, luma-test builds): which Library paths are TV
 * libraries (LibrarySelector; folders are added in Library paths only), Import
 * (TvImport: progress, then Review with Import all), the shows ("Wrong show?"),
 * and Upgrades. Importing only reads the library folders; everything goes to tv.db. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { T } from '@/theme/tokens';
import { FONT } from '@/components/start/StartUI';
import { NoticeToast } from '@/components/start/SetupToast';
import { ChevronIcon } from '@/components/nav/AppNav';
import { trackersCss } from '@/components/trackers/trackersUi';
import {
  WideCtx, card, libraryPathsCss, muted, pad, rowBtn, sectionLabel, segBtn, segOn, segment, subText, titleText, topBorder,
} from './LibraryPathsSettings';
import { ImportSection, ReviewSection, ShowPicker, FADE_MS, importCss, initialReview, type ReviewState } from './TvImport';
import { LibrarySelector } from './LibrarySelector';
import type { SelectableLibrary } from './librarySelectionApi';
import { SAVE_FAILED } from './libraryPathsApi';
import type { Candidate } from './tvLibraryApi';
import {
  TV_LIBRARIES_URL, importAll, importedText, showSubtitle, loadTv, loadTvLibrary, needsReview, rematchShow,
  saveUpgrades, showLabel, startImport,
  type ImportProgress, type Show, type TvLibrary, type TvSettingsView, type UpgradeMode,
} from './tvLibraryApi';

/** Space between sections (Libraries, Import, Review, Shows, Upgrades), as on Library paths. */
export const SECTION_GAP = 28;

export function TvLibrarySettings({ wide }: { wide: boolean }) {
  const [view, setView] = useState<TvSettingsView | null>(null);
  const [lib, setLib] = useState<TvLibrary | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [toast, setToast] = useState('');
  const [editing, setEditing] = useState<number | null>(null);     // the show whose "Wrong show?" picker is open
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [review, setReview] = useState<ReviewState>({ checked: {}, picks: {} });
  const [importingAll, setImportingAll] = useState(false);
  const [fading, setFading] = useState<Set<number>>(new Set());
  const seq = useRef(0);

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
    setView(v => (v ? { ...v, paths: sel.map(({ selected: _s, ...p }) => p) } : v));
    if (picked.current !== null && picked.current !== key) void refreshLib();   // shows follow the selection
    picked.current = key;
  }, [refreshLib]);
  useEffect(() => {
    loadTv().then(r => (r.ok ? setView(r.data) : setLoadFailed(true)));
    refreshLib();
  }, [refreshLib]);
  const onFinish = useCallback((p: ImportProgress) => {
    setImportError(p.error);
    void refreshLib().then(() => setImporting(false));
  }, [refreshLib]);

  if (!view) {
    return loadFailed ? <div role="alert" style={{ ...pad(wide), ...muted }}>{"Couldn't load TV settings"}</div> : null;
  }
  const say = (msg: string) => { seq.current += 1; setToast(msg); };
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
    say(r.data.imported.length ? importedText(r.data.imported.length) : (r.data.failed[0]?.error || SAVE_FAILED));
  };
  const reviewRows = lib?.unmatched || [];

  return (
    <WideCtx.Provider value={wide}>
    <section data-testid="tv-section" style={{ ...pad(wide), display: 'flex', flexDirection: 'column', gap: SECTION_GAP, fontFamily: FONT }}>
      <style>{libraryPathsCss}</style>
      <style>{trackersCss}</style>
      <style>{importCss}</style>
      {toast && <NoticeToast key={toast + seq.current} text={toast} onDone={() => setToast('')} />}

      <LibrarySelector url={TV_LIBRARIES_URL} label="Libraries" testId="tv-paths" onChange={onLibraries} />

      {view.paths.length > 0 && (
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
                    if (r.ok) { takeLib(r.data); setEditing(null); say('Matched'); } else say(r.error || SAVE_FAILED);
                  });
                }} />
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
