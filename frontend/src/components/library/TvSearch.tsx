/** Settings > TV > Series > Add series (v182; was the Series toolbar search): library shows
 * first ("In library", not clickable), then TMDB shows with Add. Add saves the show to
 * tv.db in one of the selected TV libraries (picked when there are 2+); it
 * creates no folders or files and downloads nothing. */
import { useEffect, useMemo, useRef, useState, type MutableRefObject } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Poster } from './TvImport';
import type { LibPath } from './libraryPathsApi';
import { showLabel, type Show } from './tvLibraryApi';
import { ADD_MONITOR, addShow, findShows, loadProfiles, seasonsText, type FindResult, type MonitorOption, type Profile } from './tvCalendarApi';
import { Select } from './TvEdit';

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

  const last = useRef<Record<number, [string | undefined, AddOpts]>>({});
  const add = async (r: FindResult, pathId?: string, opts: AddOpts = {}) => {
    last.current[r.tmdb_id] = [pathId, opts];
    setPicking(null);
    setAdds(a => ({ ...a, [r.tmdb_id]: { busy: true } }));
    const out = await addShow(r.tmdb_id, pathId, opts);
    if (out.ok) { setAdds(a => ({ ...a, [r.tmdb_id]: { series_id: out.data.series_id } })); onAdded(); return; }
    setAdds(a => ({ ...a, [r.tmdb_id]: { error: true } }));
  };
  // v158a: Add always opens the picker (Monitor and Quality profile), even with one library
  const startAdd = (r: FindResult) => setPicking(r.tmdb_id);

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
              <span style={{ ...oneLine, fontSize: 14, fontWeight: 600, color: W95 }}>{showLabel(r)}</span>
              {r.seasons != null && <span style={{ ...oneLine, fontSize: 13, color: W50 }}>{seasonsText(r.seasons)}</span>}
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
                  <button type="button" className="lp-focus tvs-add" data-testid="tv-add" aria-haspopup="dialog"
                    aria-expanded={picking === r.tmdb_id}
                    onClick={() => startAdd(r)} style={addBtn}>Add</button>
                )}
              </div>
            )}
            {st.error && (
              <div role="alert" data-testid="tv-add-error"
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 12px 12px 64px', fontSize: 13, color: '#ef4444' }}>
                {ADD_FAILED}
                <button type="button" className="lp-focus" data-testid="tv-add-retry" onClick={() => {
                  const l = last.current[r.tmdb_id];
                  if (l) void add(r, l[0], l[1]); else startAdd(r);
                }} style={retryPill}>Retry</button>
              </div>
            )}
            {picking === r.tmdb_id && (
              <PathPicker inline paths={paths} onCancel={() => setPicking(null)} onAdd={(p, o) => void add(r, paths.length >= 2 ? p : undefined, o)} />
            )}
          </div>
        );
      })}
    </div>
  );
}

/** v181 (Taylor): the calendar toolbar search finds only your own shows (the series in tv.db, from the library
 * already loaded): no TVDB or TMDB lookup and no unowned results. A result opens the show; adding shows stays on
 * the Series page search. */
export const ownedKey = (t: string) => (t || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
  .replace(/[.'\u2019\u2018`]/g, '').replace(/[^0-9a-z]+/g, ' ').trim();
export const OWNED_MAX = 20;
export function ownedMatches(shows: Show[], q: string): Show[] {
  const k = ownedKey(q);
  if (k.length < SEARCH_MIN) return [];
  const words = k.split(' ');
  const scored: [number, Show][] = [];
  for (const s of shows) {
    const t = ownedKey(s.title);
    const bare = t.replace(/^(?:the|a|an) (?=\S)/, '');
    if (!words.every(w => t.includes(w))) continue;
    const rank = t === k || bare === k ? 0 : t.startsWith(k) || bare.startsWith(k) ? 1 : t.split(' ').some(w => w.startsWith(words[0] ?? k)) ? 2 : 3;
    scored.push([rank, s]);
  }
  return scored.sort((a, b) => a[0] - b[0] || ownedKey(a[1].title).localeCompare(ownedKey(b[1].title)) || (a[1].year ?? 0) - (b[1].year ?? 0))
    .slice(0, OWNED_MAX).map(x => x[1]);
}

export function TvOwnedSearch({ q, shows, onOpen, active = -1, keys }: {
  q: string; shows: Show[] | null; onOpen: (s: Show) => void;
  active?: number; keys?: MutableRefObject<SearchKeys | null>;
}) {
  const list = useMemo(() => ownedMatches(shows || [], q), [shows, q]);
  if (keys) keys.current = { count: list.length, enter: i => { const s = list[i]; if (s) onOpen(s); } };
  return (
    <div data-testid="tv-owned-results" id={SEARCH_LIST_ID} role="listbox" aria-label="Your shows"
      style={{ fontFamily: FONT, width: '100%', boxSizing: 'border-box' }}>
      {shows === null ? (
        <div data-testid="tv-search-loading" aria-busy="true" style={{ ...rowBox, background: 'rgba(255,255,255,0.04)' }} />
      ) : !list.length ? (
        <div data-testid="tv-search-empty" style={centered}>No shows</div>
      ) : list.map((s, i) => (
        <button key={s.id} type="button" data-testid="tv-owned-row" data-show-id={s.id} id={searchRowId(i)} role="option"
          aria-selected={i === active} data-active={i === active || undefined} className="lp-focus tvs-owned"
          onClick={() => onOpen(s)}
          style={{ ...rowBox, width: '100%', border: 'none', borderTop: i ? divider : 'none', cursor: 'pointer', textAlign: 'left', fontFamily: FONT,
            background: i === active ? 'rgba(255,255,255,0.06)' : 'transparent' }}>
          <Poster path={s.poster_path} w={40} h={60} tmdbLogo />
          <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
            <span style={{ ...oneLine, fontSize: 14, fontWeight: 600, color: W95 }}>{showLabel(s)}</span>
            <span style={{ ...oneLine, fontSize: 13, color: W50 }}>{`${Math.min(s.have ?? 0, s.aired ?? 0)} of ${s.aired ?? 0} episodes`}</span>
          </span>
          {!s.monitored && <span style={{ fontSize: 12, color: W50, flex: 'none' }}>Unmonitored</span>}
        </button>
      ))}
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

type AddOpts = { monitor?: MonitorOption; profile_id?: number | null };

/** "Add to" (v158a: 360px): Monitor (All episodes by default) and Quality profile (the default one), then the
 * selected TV libraries (radios with 2+, nothing preselected; the path line alone with one). */
export function PathPicker({ paths, onCancel, onAdd, inline = false }: {
  paths: LibPath[]; onCancel: () => void; onAdd: (pathId: string, opts: AddOpts) => void;
  /** in the search dropdown (a scroll box): in the flow under the row, not a popover */
  inline?: boolean;
}) {
  const one = paths.length === 1 ? paths[0] : null;
  const [pick, setPick] = useState<string | null>(one ? one.id : null);
  const [monitor, setMonitor] = useState<MonitorOption>('future');     // v182: Future episodes by default
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profile, setProfile] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    void loadProfiles().then(r => {
      if (!live || !r.ok) return;
      setProfiles(r.data.profiles);
      setProfile(p => p ?? r.data.profiles.find(x => x.default)?.id ?? null);
    });
    return () => { live = false; };
  }, []);
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
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <span id="tv-add-monitor-label" style={pickLabel}>Monitor</span>
        <Select id="tv-add-monitor" value={monitor} options={ADD_MONITOR} onChange={setMonitor} />
      </div>
      {profiles.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span id="tv-add-profile-label" style={pickLabel}>Quality profile</span>
          <Select id="tv-add-profile" value={profile} options={profiles.map(p => ({ id: p.id, label: p.name }))} onChange={setProfile} />
        </div>
      )}
      {one ? (
        <div data-testid="tv-add-path-line" style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '0 4px' }}>
          <span id="tv-add-to" style={pickLabel}>Add to</span>
          <span style={{ fontSize: 14, fontWeight: 500, color: W95 }}>{one.name}</span>
          <span style={{ fontSize: 12, color: W50, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{one.path}</span>
        </div>
      ) : (<>
      <div id="tv-add-to" style={{ ...pickLabel, padding: '0 4px' }}>Add to</div>
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
                <span style={{ ...oneLine, fontSize: 14, fontWeight: 500, color: W95 }}>{p.name}</span>
                <span style={{ ...oneLine, fontSize: 12, color: W50 }}>{p.path}</span>
              </span>
            </button>
          );
        })}
      </div>
      </>)}
      <div style={{ display: 'flex', justifyContent: 'center', gap: 12 }}>
        <button type="button" className="lp-focus" data-testid="tv-add-cancel" onClick={onCancel} style={sheetBtn(false)}>Cancel</button>
        <button type="button" className="lp-focus" data-testid="tv-add-confirm" disabled={!pick}
          onClick={() => pick && onAdd(pick, { monitor, profile_id: profile })} style={{ ...sheetBtn(true), opacity: pick ? 1 : 0.4, cursor: pick ? 'pointer' : 'default' }}>Add</button>
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
        : { ...surface, position: 'absolute', zIndex: 50, right: 12, top: 'calc(100% - 8px)', width: 360, maxWidth: 'calc(100vw - 32px)',
          padding: 12, borderRadius: 14, boxShadow: '0 16px 48px rgba(0,0,0,0.5)' }}>
      {body}
    </div>
  );
}

export const tvSearchCss = `
.tvs-add:hover{background:#60a5fa!important}
.tvs-owned:hover{background:rgba(255,255,255,0.06)!important}
`;

const oneLine: React.CSSProperties = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
const divider = '1px solid rgba(255,255,255,0.06)';
const pickLabel: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: W50 };
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
