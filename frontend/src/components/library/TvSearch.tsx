/** Settings > TV > Search (docs/design/tv.md, v1, desktop web): library shows
 * first ("In library", not clickable), then TMDB shows with Add. Add saves the show to
 * tv.db in one of the selected TV libraries (picked when there are 2+); it
 * creates no folders or files and downloads nothing. */
import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Poster } from './TvImport';
import type { LibPath } from './libraryPathsApi';
import { showLabel } from './tvLibraryApi';
import { addShow, findShows, seasonsText, type FindResult } from './tvCalendarApi';

/** Designer: the search dropdown and the Add path picker share one surface. */
export const TV_POPOVER_SURFACE: React.CSSProperties = {
  background: 'rgba(21,16,42,0.92)', border: '1px solid rgba(255,255,255,0.08)',
  backdropFilter: 'blur(24px)', WebkitBackdropFilter: 'blur(24px)',
};

export const SEARCH_DEBOUNCE_MS = 250;
export const SEARCH_MIN = 2;
export const ADD_FAILED = "Couldn't add show";
const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';

type AddState = { busy?: boolean; error?: boolean; series_id?: number };
/** What the search box drives by keyboard: how many rows there are, and Enter on row *i*. */
export type SearchKeys = { count: number; enter: (i: number) => void };
export const SEARCH_LIST_ID = 'tv-search-list';
export const searchRowId = (i: number) => `tv-search-row-${i}`;

export function TvSearch({ q, paths, onAdded, active = -1, keys }: {
  q: string; paths: LibPath[]; onAdded: () => void;
  /** the keyboard-highlighted row (the box's ↑/↓), -1 for none */
  active?: number; keys?: MutableRefObject<SearchKeys | null>;
}) {
  const [res, setRes] = useState<{ q: string; results: FindResult[] } | null>(null);
  const [failed, setFailed] = useState('');
  const [adds, setAdds] = useState<Record<number, AddState>>({});
  const [picking, setPicking] = useState<number | null>(null);
  const text = q.trim();
  useEffect(() => {
    if (text.length < SEARCH_MIN) return undefined;
    let live = true;
    const t = window.setTimeout(() => {
      void findShows(text).then(r => {
        if (!live) return;
        if (r.ok) { setRes({ q: text, results: r.data.results }); setFailed(''); } else setFailed(text);
      });
    }, SEARCH_DEBOUNCE_MS);
    return () => { live = false; window.clearTimeout(t); };
  }, [text]);

  const add = async (r: FindResult, pathId?: string) => {
    setPicking(null);
    setAdds(a => ({ ...a, [r.tmdb_id]: { busy: true } }));
    const out = await addShow(r.tmdb_id, pathId);
    if (out.ok) { setAdds(a => ({ ...a, [r.tmdb_id]: { series_id: out.data.series_id } })); onAdded(); return; }
    setAdds(a => ({ ...a, [r.tmdb_id]: { error: true } }));
  };
  const startAdd = (r: FindResult) => {
    if (paths.length >= 2) setPicking(r.tmdb_id);
    else void add(r);
  };

  const ready = res?.q === text;
  const list = ready && failed !== text ? res.results : [];
  if (keys) {
    keys.current = {
      count: list.length,
      enter: i => {
        const r = list[i];
        if (r && !r.in_library && adds[r.tmdb_id]?.series_id == null && !adds[r.tmdb_id]?.busy) startAdd(r);
      },
    };
  }
  return (
    <div data-testid="tv-search-results" id={SEARCH_LIST_ID} role="listbox" aria-label="Search results"
      style={{ fontFamily: FONT, width: '100%', boxSizing: 'border-box' }}>
      {failed === text ? (
        <div role="alert" style={centered}>{"Couldn't search"}</div>
      ) : !ready ? (
        <div data-testid="tv-search-loading" aria-busy="true">
          {[0, 1, 2].map(i => (
            <div key={i} style={{ ...rowBox, background: 'rgba(255,255,255,0.04)', borderTop: i ? divider : 'none' }}>
            </div>
          ))}
        </div>
      ) : !list.length ? (
        <div data-testid="tv-search-empty" style={centered}>No results</div>
      ) : list.map((r, i) => {
        const st = adds[r.tmdb_id] || {};
        const inLib = r.in_library || st.series_id != null;
        const body = (
          <>
            <Poster path={r.poster_path} w={40} h={60} tmdbLogo />
            <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ ...one, fontSize: 14, fontWeight: 600, color: W95 }}>{showLabel(r)}</span>
              {r.seasons != null && <span style={{ ...one, fontSize: 13, color: W50 }}>{seasonsText(r.seasons)}</span>}
            </span>
          </>
        );
        return (
          <div key={`${r.in_library ? 'l' : 't'}${r.tmdb_id}`} data-testid="tv-search-row" data-in-library={inLib || undefined}
            id={searchRowId(i)} role="option" aria-selected={i === active} data-active={i === active || undefined}
            style={{ position: 'relative', borderTop: i ? divider : 'none', background: i === active ? 'rgba(255,255,255,0.06)' : undefined }}>
            {inLib ? (
              <div style={rowBox}>
                {body}
                <InLibrary />
              </div>
            ) : (
              <div style={rowBox}>
                {body}
                {st.busy ? (
                  <span data-testid="tv-add-busy" aria-label="Adding" role="status" style={{ ...addBtn, background: 'rgba(59,130,246,0.4)' }}>
                    <span className="tvi-spin" aria-hidden="true" />
                  </span>
                ) : (
                  <button type="button" className="lp-focus tvs-add" data-testid="tv-add" aria-haspopup={paths.length >= 2 ? 'dialog' : undefined}
                    aria-expanded={paths.length >= 2 ? picking === r.tmdb_id : undefined}
                    onClick={() => startAdd(r)} style={addBtn}>Add</button>
                )}
              </div>
            )}
            {st.error && (
              <div role="alert" data-testid="tv-add-error"
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 12px 12px 64px', fontSize: 13, color: '#ef4444' }}>
                {ADD_FAILED}
                <button type="button" className="lp-focus" data-testid="tv-add-retry" onClick={() => startAdd(r)} style={retryPill}>Retry</button>
              </div>
            )}
            {picking === r.tmdb_id && (
              <PathPicker inline paths={paths} onCancel={() => setPicking(null)} onAdd={p => void add(r, p)} />
            )}
          </div>
        );
      })}
    </div>
  );
}

export const InLibrary = () => (
  <span data-testid="tv-in-library" style={{
    display: 'inline-flex', alignItems: 'center', gap: 6, height: 22, padding: '0 10px', borderRadius: 999, flex: 'none',
    background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', fontSize: 12, fontWeight: 600, color: W95,
  }}>
    <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: 3, background: '#22c55e' }} />
    In library
  </span>
);

/** "Add to": the selected TV libraries, nothing preselected (a popover under the row). */
export function PathPicker({ paths, onCancel, onAdd, inline = false }: {
  paths: LibPath[]; onCancel: () => void; onAdd: (pathId: string) => void;
  /** in the search dropdown (a scroll box): in the flow under the row, not a popover */
  inline?: boolean;
}) {
  const [pick, setPick] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onCancel(); } };
    const down = (e: PointerEvent) => {
      const t = e.target as HTMLElement;
      if (panel.current?.contains(t) || t.closest?.('.tvs-add')) return;
      onCancel();
    };
    document.addEventListener('keydown', key, true);
    document.addEventListener('pointerdown', down, true);
    return () => { document.removeEventListener('keydown', key, true); document.removeEventListener('pointerdown', down, true); };
  }, [onCancel]);
  const body = (
    <>
      <div id="tv-add-to" style={{ fontSize: 13, fontWeight: 600, color: W50, padding: '0 4px' }}>Add to</div>
      <div role="radiogroup" aria-labelledby="tv-add-to" style={{ display: 'flex', flexDirection: 'column' }}>
        {paths.map(p => {
          const on = pick === p.id;
          return (
            <button key={p.id} type="button" role="radio" aria-checked={on} data-testid="tv-add-path" className="lp-focus tvc-row"
              onClick={() => setPick(p.id)}
              style={{ display: 'flex', alignItems: 'center', gap: 12, minHeight: 52, padding: '6px 8px', border: 'none', borderRadius: 10,
                background: 'none', cursor: 'pointer', textAlign: 'left', fontFamily: FONT, boxSizing: 'border-box', width: '100%' }}>
              <span aria-hidden="true" style={{
                width: 20, height: 20, borderRadius: 10, flex: 'none', boxSizing: 'border-box',
                border: on ? '6px solid #3b82f6' : '2px solid rgba(255,255,255,0.35)', background: on ? '#fff' : 'none',
              }} />
              <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                <span style={{ ...one, fontSize: 14, fontWeight: 500, color: W95 }}>{p.name}</span>
                <span style={{ ...one, fontSize: 12, color: W50 }}>{p.path}</span>
              </span>
            </button>
          );
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 12 }}>
        <button type="button" className="lp-focus" data-testid="tv-add-cancel" onClick={onCancel} style={sheetBtn(false)}>Cancel</button>
        <button type="button" className="lp-focus" data-testid="tv-add-confirm" disabled={!pick}
          onClick={() => pick && onAdd(pick)} style={{ ...sheetBtn(true), opacity: pick ? 1 : 0.4, cursor: pick ? 'pointer' : 'default' }}>Add</button>
      </div>
    </>
  );
  const surface: React.CSSProperties = {
    display: 'flex', flexDirection: 'column', gap: 12, boxSizing: 'border-box', ...TV_POPOVER_SURFACE, fontFamily: FONT,
  };
  return (
    <div ref={panel} role="dialog" aria-label="Add to" data-testid="tv-add-picker"
      style={inline
        ? { ...surface, margin: '0 12px 12px', padding: 12, borderRadius: 14 }
        : { ...surface, position: 'absolute', zIndex: 50, right: 12, top: 'calc(100% - 8px)', width: 320, maxWidth: 'calc(100vw - 32px)',
          padding: 12, borderRadius: 14, boxShadow: '0 16px 48px rgba(0,0,0,0.5)' }}>
      {body}
    </div>
  );
}

export const tvSearchCss = `
.tvs-add:hover{background:#60a5fa!important}
`;

const one: React.CSSProperties = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
const divider = '1px solid rgba(255,255,255,0.06)';
const rowBox: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12, height: 72, padding: '0 12px', boxSizing: 'border-box' };
const centered: React.CSSProperties = {
  minHeight: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 500, color: W70,
};
const addBtn: React.CSSProperties = {
  height: 32, minWidth: 64, padding: '0 16px', borderRadius: 10, border: 'none', background: '#3b82f6',
  color: '#fff', fontSize: 14, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', flex: 'none',
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', boxSizing: 'border-box',
};
const retryPill: React.CSSProperties = {
  height: 28, padding: '0 12px', borderRadius: 999, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.12)',
  color: '#fff', fontSize: 13, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', flex: 'none',
};
const sheetBtn = (primary: boolean): React.CSSProperties => ({
  height: 36, minWidth: 104, padding: '0 18px', borderRadius: 10, fontSize: 15, fontWeight: 600, fontFamily: FONT, cursor: 'pointer',
  border: primary ? 'none' : '1px solid rgba(255,255,255,0.12)', background: primary ? '#3b82f6' : 'rgba(255,255,255,0.06)', color: '#fff',
});
