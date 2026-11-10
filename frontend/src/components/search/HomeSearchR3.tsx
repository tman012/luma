/**
 * v227 R3 (Taylor's pick): desktop and tablet Home search (>= 1024px, the kiosk Home), with the top nav bar
 * (DesktopDock). v358: the bar's Search button (and '/', `open-search`) opens a centered search modal over the
 * page; nothing drops down under the bar. The modal holds the same field and the same results: filter chips over
 * one grid of titles (your library first, then the TMDb titles from the existing TMDb search path, performSearch's
 * /search?type=all; word-match filtered as C2), then people. A result opens its sheet over the modal. Focus is
 * kept inside the modal, Escape or a backdrop click closes it, and focus goes back to the Search button.
 * No key badges, no helper text. Phones keep their overlay.
 * Same data rules as the C2 palette: performSearch, 300 ms debounce, 2-character minimum, abort on retype.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { FONT } from '@/components/start/StartUI';
import { DesktopDock } from '@/components/home/DesktopDock';
import { CLOSE_SYMBOL, SYMBOL_VIEWBOX } from '@/assets/desktop-nav/symbols';
import { useModalHost } from '@/contexts/ModalHostContext';
import {
  openResult, resultIds, warmResult, warmResults, resultWarmKey,
  performSearch, getPosterUrl, getProfileUrl, getTitle, getYear, TMDB_BUSY_TEXT,
  type SearchMovie, type SearchPerson, type SearchResults,
} from '@/services/searchApi';
import { sizedPlexImg } from '@/utils/image';
import { isMinimal } from '@/minimal';
import { fetchNativeWatchlistIdsOrNull } from '@/services/watchlistApi';
import { PathPicker } from '@/components/library/TvSearch';
import { loadTv, loadTvLibrary } from '@/components/library/tvLibraryApi';
import { addShow, loadProfiles, type MonitorOption } from '@/components/library/tvCalendarApi';
import type { LibPath } from '@/components/library/libraryPathsApi';
import { WatchlistAddSheet } from '@/components/movies/WatchlistAddSheet';
import { DEBOUNCE_MS, MIN_QUERY, SKELETON_DELAY_MS, unownedRows } from './HomeSearchPalette';

type Phase = 'idle' | 'loading' | 'done' | 'error';

export type R3View = {
  top: SearchMovie | null;
  grid: SearchMovie[];
  people: SearchPerson[];
  more: SearchMovie[];
};

/** The grid holds up to this many titles: your library first, then TMDb titles (as the mock). */
export const GRID_MAX = 6;

/** Top result: the first title in your library, else the first TMDb title (then left out of Find more).
 * v228 (PM): the grid is the rest of your library, then TMDb titles up to GRID_MAX; Find more lists every
 * TMDb title (its count is all of them). */
export function buildR3(res: SearchResults | null, q: string): R3View {
  if (!res) return { top: null, grid: [], people: [], more: [] };
  const tmdb = unownedRows(res, 'all', q).map(r => r.item as SearchMovie);
  const owned = res.owned;
  const top = owned[0] ?? tmdb[0] ?? null;
  const tmdbRest = owned.length ? tmdb : tmdb.slice(1);
  const rest = owned.slice(1, 1 + GRID_MAX);
  const grid = [...rest, ...tmdbRest.slice(0, Math.max(0, GRID_MAX - rest.length))];
  // v231 (PM): On TMDb (and Find more's count) lists only titles not already shown above
  const shown = new Set([top, ...grid].filter(Boolean).map(m => r3Key(m!)));
  const more = tmdbRest.filter(m => !shown.has(r3Key(m)));
  return { top, grid, people: [...res.ownedPeople, ...res.unownedPeople], more };
}

/** The watchlist '+' shows on TMDb movie cards on the test install (the native watchlist is movies only). */
const r3Key = (m: SearchMovie) => `${m.media_type === 'tv' ? 'tv' : 'movie'}-${m.tmdb_id || m.id}`;
/** v231 (PM): the TMDb id a TV card adds to the TV library with (not already in your Plex library). */
export const tvAddId = (m: SearchMovie): number | null => (!m.isOwned && m.media_type === 'tv' && (m.tmdb_id || m.id)) || null;
export const wlId = (m: SearchMovie): number | null =>
  (!m.isOwned && m.media_type !== 'tv' && (m.tmdb_id || m.id)) || null;

const kind = (m: SearchMovie) => (m.media_type === 'tv' ? 'TV' : 'Movie');
export const metaR3 = (m: SearchMovie) => { const y = getYear(m); return y ? `${y} · ${kind(m)}` : kind(m); };

export function HomeSearchR3() {
  const { openMovie, openTv } = useModalHost();
  // v228 (PM): the existing native watchlist state and add flow, as KioskSearch (v202): ids, then the profile sheet
  const [wlIds, setWlIds] = useState<Set<number> | null>(null);
  const [wlAdd, setWlAdd] = useState<SearchMovie | null>(null);
  // v231 (Tester): no movie Add until the id lookup succeeds (a 404 or error leaves it hidden)
  useEffect(() => { if (isMinimal()) void fetchNativeWatchlistIdsOrNull().then(setWlIds); }, []);
  // v231 (PM): TV cards add the series as Add series does (tv.db); needs the TV paths and the library's ids
  const [tv, setTv] = useState<{ paths: LibPath[]; ids: Set<number> } | null>(null);
  const [tvAdd, setTvAdd] = useState<SearchMovie | null>(null);
  const [tvBusy, setTvBusy] = useState(false);
  useEffect(() => {
    if (!isMinimal()) return;
    let live = true;
    void Promise.all([loadTv(), loadTvLibrary()]).then(([v, l]) => {
      if (live && v.ok && l.ok && v.data.paths.length) setTv({ paths: v.data.paths, ids: new Set(l.data.shows.map(x => x.tmdb_id)) });
    });
    return () => { live = false; };
  }, []);
  const wlFor = (m: SearchMovie) => {
    const t = tvAddId(m);
    if (t) return tv ? { inList: tv.ids.has(t), onAdd: () => (tv.paths.length === 1 ? void addTvDirect(m) : setTvAdd(m)), tv: true } : undefined;
    const id = wlId(m);
    return wlIds && id ? { inList: wlIds.has(id), onAdd: () => setWlAdd(m) } : undefined;
  };
  // v231b (PM): with exactly one TV path, + adds at once with Add series' defaults (Future episodes, default profile)
  const addTvDirect = async (m: SearchMovie) => {
    if (!tv || tvBusy) return;
    const pr = await loadProfiles();
    const profile_id = pr.ok ? pr.data.profiles.find(x => x.default)?.id ?? null : null;
    await addTv(m, tv.paths[0]!.id, { monitor: 'future', profile_id });
  };
  const addTv = async (m: SearchMovie, pathId: string, opts: { monitor?: MonitorOption; profile_id?: number | null }) => {
    const id = tvAddId(m);
    if (!id || !tv || tvBusy) return;
    setTvBusy(true);
    const out = await addShow(id, tv.paths.length >= 2 ? pathId : undefined, opts);
    setTvBusy(false);
    if (out.ok) { setTv(p => p && { ...p, ids: new Set([...p.ids, id]) }); setTvAdd(null); }
  };
  const [query, setQuery] = useState('');
  const [res, setRes] = useState<SearchResults | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [skeleton, setSkeleton] = useState(false);
  const [, setExpanded] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const debRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const skelRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastQ = useRef('');

  const run = useCallback(async (q: string) => {
    abortRef.current?.abort();
    if (skelRef.current) clearTimeout(skelRef.current);
    lastQ.current = q;
    setExpanded(false);
    if (q.length < MIN_QUERY) { setPhase('idle'); setRes(null); setSkeleton(false); return; }
    const ctl = new AbortController();
    abortRef.current = ctl;
    setPhase('loading');
    setSkeleton(false);
    skelRef.current = setTimeout(() => setSkeleton(true), SKELETON_DELAY_MS);
    try {
      const r = await performSearch(q, ctl.signal);
      if (ctl.signal.aborted) return;
      setRes(r); setPhase(r.failed ? 'error' : 'done');
    } catch (e) {
      if ((e as { name?: string })?.name === 'AbortError' || ctl.signal.aborted) return;
      setPhase('error');
    } finally {
      if (skelRef.current) clearTimeout(skelRef.current);
      if (!ctl.signal.aborted) setSkeleton(false);
    }
  }, []);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    if (debRef.current) clearTimeout(debRef.current);
    setQuery(''); setRes(null); setPhase('idle'); setExpanded(false); lastQ.current = '';
  }, []);

  const searching = query.trim().length >= MIN_QUERY;
  // The modal is closed until Search opens it (or while a query is typed).
  const [modal, setModal] = useState(false);
  const shown = modal || searching;
  const panelRef = useRef<HTMLDivElement>(null);
  const searchBtnRef = useRef<HTMLButtonElement>(null);
  const openModal = useCallback(() => { flushSync(() => setModal(true)); inputRef.current?.focus(); }, []);
  // Closing hands focus back to the Search button, unless the page is changing or another surface takes over.
  const closeModal = useCallback((refocus = true) => {
    setModal(false); clear();
    if (refocus) searchBtnRef.current?.focus(); else inputRef.current?.blur();
  }, [clear]);

  // '/' and the header's open-search open the modal (the C2 palette stands down while this is mounted)
  useEffect(() => {
    window.addEventListener('open-search', openModal);
    return () => window.removeEventListener('open-search', openModal);
  }, [openModal]);
  useEffect(() => () => { abortRef.current?.abort(); if (debRef.current) clearTimeout(debRef.current); }, []);

  // The add sheets (watchlist profile, TV path) sit over the modal and take Escape first.
  const overSheet = useRef(false);
  overSheet.current = !!(wlAdd || tvAdd);
  useEffect(() => {
    if (!shown) return;
    const onKeyDoc = (e: KeyboardEvent) => {
      // A sheet/modal open over R3 owns the keyboard: only it closes, R3 stays (Tester, v326).
      if (e.defaultPrevented || document.querySelector('[data-modal]')) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        if (overSheet.current) { setWlAdd(null); setTvAdd(null); } else closeModal();
        return;
      }
      if (e.key !== 'Tab' || overSheet.current) return;
      // Focus trap: Tab and Shift+Tab stay inside the dialog.
      const panel = panelRef.current;
      if (!panel) return;
      const stops = modalTabStops(panel);
      const first = stops[0], last = stops[stops.length - 1];
      const active = document.activeElement;
      if (!first || !last) { e.preventDefault(); return; }
      if (!panel.contains(active)) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
      else if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKeyDoc);
    return () => document.removeEventListener('keydown', onKeyDoc);
  }, [shown, closeModal]);
  // Body scroll lock while the modal is open (re-asserted when the tab comes back, as useBodyScroll does).
  useEffect(() => {
    if (!shown) return;
    const html = document.documentElement;
    const body = document.body;
    const prevHtml = html.style.overflow;
    const prevBody = body.style.overflow;
    const lock = () => { html.style.overflow = 'hidden'; body.style.overflow = 'hidden'; };
    lock();
    const onVis = () => { if (document.visibilityState === 'visible') lock(); };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      document.removeEventListener('visibilitychange', onVis);
      html.style.overflow = prevHtml;
      body.style.overflow = prevBody;
    };
  }, [shown]);
  const downOnBackdrop = useRef(false);
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('home-search', { detail: { searching } }));
  }, [searching]);
  const view = useMemo(() => buildR3(res, lastQ.current), [res]);

  // Same open as a Home card tap (openWhenReady through openResult).
  const open = (item: SearchMovie, el?: HTMLElement | null) => {
    const { ratingKey, tmdbId } = resultIds(item);
    const opts = { ratingKey, tmdbId, origin: 'search' as const };
    if (!opts.ratingKey && !opts.tmdbId) return;
    void openResult(item, el, () => { if (item.media_type === 'tv') openTv(opts); else openMovie(opts); });
  };
  useEffect(() => {
    if (!res) return;
    return warmResults([...res.owned, ...res.unowned]);
  }, [res]);
  // The person view is its own full-screen surface (KioskSearch): the modal closes as it opens, as the C2 palette does.
  const openPerson = (p: SearchPerson) => {
    closeModal(false);
    window.dispatchEvent(new CustomEvent('search-query', { detail: { query: p.name, tab: 'people', personId: p.id } }));
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && phase === 'done' && view.top) { e.preventDefault(); open(view.top); }
  };

  const q = lastQ.current;
  const nothing = phase === 'done' && !view.top && view.people.length === 0;
  return (
    <div data-r3-search data-testid="r3-search" data-searching={searching ? 'true' : 'false'} style={{ fontFamily: FONT }}>
      <style>{r3Css}</style>
      {wlAdd && wlId(wlAdd) && (
        <WatchlistAddSheet tmdbId={wlId(wlAdd)!} title={getTitle(wlAdd)} year={getYear(wlAdd) || null} onClose={() => setWlAdd(null)}
          onAdded={() => { const id = wlId(wlAdd)!; setWlIds(p => new Set([...(p ?? []), id])); setWlAdd(null); }} />
      )}
      {tvAdd && tv && (
        <div className="r3-tvadd" data-testid="r3-tv-add-sheet">
          <div className="r3-tvadd-in">
            <div className="r3-tvadd-t">{`Add ${getTitle(tvAdd)}`}</div>
            <PathPicker inline paths={tv.paths} onCancel={() => setTvAdd(null)} onAdd={(p, o) => void addTv(tvAdd, p, o)} />
          </div>
        </div>
      )}
      <DesktopDock searchOpen={shown} searchRef={searchBtnRef} onBeforeNavigate={() => closeModal(false)} onSearch={openModal} />
      {/* Always mounted (the field keeps its state); closed it is inert and out of the accessibility tree. */}
      <div className="r3-modal" data-testid="r3-modal" data-open={shown ? 'true' : 'false'} inert={!shown} aria-hidden={shown ? undefined : true}>
        <div className="r3-backdrop" data-testid="search-backdrop"
          onMouseDown={() => { downOnBackdrop.current = true; }}
          onClick={() => { if (downOnBackdrop.current) closeModal(); downOnBackdrop.current = false; }} />
        <div ref={panelRef} className="r3-panel" role="dialog" aria-modal="true" aria-label="Search" data-testid="search-modal"
          data-searching={searching ? 'true' : 'false'} onMouseDown={() => { downOnBackdrop.current = false; }}>
          <div className="r3-head" data-testid="r3-head">
            <label className="r3-field" data-focus-ring>
              <span className="r3-icon"><H2SearchIcon /></span>
              <input ref={inputRef} className="r3-input" data-testid="search-input" type="search" placeholder="Search movies and shows" aria-label="Search"
                value={query} autoComplete="off" spellCheck={false} onKeyDown={onKey}
                onChange={e => {
                  const v = e.target.value; setQuery(v);
                  if (debRef.current) clearTimeout(debRef.current);
                  debRef.current = setTimeout(() => void run(v.trim()), DEBOUNCE_MS);
                }} />
            </label>
            <button type="button" className="r3-close" data-testid="search-close" aria-label="Close" onClick={() => closeModal()}>
              <svg width={22} height={22} viewBox={SYMBOL_VIEWBOX} fill="currentColor" aria-hidden="true" focusable="false"><path d={CLOSE_SYMBOL} /></svg>
            </button>
          </div>
          {searching && (
            <div className="r3-body" data-testid="r3-results" data-state={phase === 'loading' ? (skeleton ? 'loading' : 'pending') : nothing ? 'empty' : phase}>
              {phase === 'loading' && skeleton && (
                <div className="r3-main" data-testid="search-reserved" aria-hidden="true">
                  <div className="r3-top r3-sk" style={{ height: 'calc(var(--r3-top) * 0.56 + 150px)' }} />
                  <div className="r3-grid">{[0, 1, 2, 3, 4].map(i => <div key={i} className="r3-po r3-sk" />)}</div>
                </div>
              )}
              {nothing && <div className="r3-empty" data-testid="search-empty">{res?.rateLimited ? TMDB_BUSY_TEXT : 'No results'}</div>}
              {phase === 'error' && (
                <div className="r3-empty" data-testid="search-error">
                  <div>Couldn't search</div>
                  <button type="button" className="r3-btn r3-primary" style={{ marginTop: 16 }} onClick={() => void run(lastQ.current)}>Try again</button>
                </div>
              )}
              {phase === 'done' && !nothing && (
                <div className="luma-fade-in"><D3Results key={q} titles={[...(view.top ? [view.top] : []), ...view.grid, ...view.more]} people={view.people}
                  onOpen={open} onPerson={openPerson} wlFor={wlFor} /></div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** What Tab can reach inside the search modal, in order. */
export function modalTabStops(panel: HTMLElement): HTMLElement[] {
  return [...panel.querySelectorAll<HTMLElement>('input, button, a[href], select, textarea, [tabindex]')]
    .filter(el => !(el as HTMLButtonElement).disabled && el.tabIndex >= 0 && !el.closest('[aria-hidden="true"], [inert]'));
}

type D3Filter = 'all' | 'movie' | 'tv' | 'people' | 'owned';
type WlFor = (m: SearchMovie) => { inList: boolean; onAdd: () => void; tv?: boolean } | undefined;

/** v246 D3 (Taylor-approved): filter chips over one 7-column grid (titles, then people); filtered on the client. */
function D3Results({ titles, people, onOpen, onPerson, wlFor }: {
  titles: SearchMovie[]; people: SearchPerson[]; onOpen: (m: SearchMovie, el?: HTMLElement | null) => void; onPerson: (p: SearchPerson) => void; wlFor: WlFor;
}) {
  const [f, setF] = useState<D3Filter>('all');
  const movies = titles.filter(m => m.media_type !== 'tv');
  const tv = titles.filter(m => m.media_type === 'tv');
  const owned = titles.filter(m => m.isOwned);
  const chips: { id: D3Filter; label: string; n: number }[] = [
    { id: 'all', label: 'All', n: titles.length + people.length },
    { id: 'movie', label: 'Movies', n: movies.length },
    { id: 'tv', label: 'TV', n: tv.length },
    { id: 'people', label: 'People', n: people.length },
    { id: 'owned', label: 'In library', n: owned.length },
  ];
  const shownTitles = f === 'all' ? titles : f === 'movie' ? movies : f === 'tv' ? tv : f === 'owned' ? owned : [];
  const shownPeople = f === 'all' || f === 'people' ? people : [];
  return (
    <div className="d3" data-testid="d3">
      <div className="d3-chips" role="group" aria-label="Filter results" data-testid="d3-chips">
        {chips.map(c => (
          <button key={c.id} type="button" className="d3-chip" data-testid={`d3-chip-${c.id}`} aria-pressed={f === c.id}
            onClick={() => setF(c.id)}>{`${c.label} ${c.n}`}</button>
        ))}
      </div>
      <div className="d3-grid" data-testid="r3-grid">
        {shownTitles.map((m, i) => <PosterCard key={`${m.media_type}-${m.rating_key ?? m.id}-${i}`} item={m} onOpen={(el) => onOpen(m, el)} wl={wlFor(m)} />)}
        {shownPeople.map(p => {
          const img = getProfileUrl(p);
          return (
            <button key={`${p.id}-${p.name}`} type="button" className="d3-person" data-testid="r3-person" onClick={() => onPerson(p)}>
              <span className="d3-avatar">{img && <img src={img} alt="" />}</span>
              <span className="r3-ct">{p.name}</span>
              <span className="r3-cm">Person</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function PosterCard({ item, onOpen, wl }: { item: SearchMovie; onOpen: (el: HTMLElement) => void; wl?: { inList: boolean; onAdd: () => void; tv?: boolean } }) {
  const img = sizedPlexImg(getPosterUrl(item), 272, 408);
  const title = getTitle(item);
  const [broken, setBroken] = useState<string | null>(null);   // the URL that failed
  return (
    <div className="r3-cardw">
      <button type="button" className="r3-card" data-testid="r3-card" data-warm-key={resultWarmKey(item)} data-owned={item.isOwned ? 'true' : 'false'} onClick={(e) => onOpen(e.currentTarget)}
        onPointerEnter={() => { void warmResult(item); }} onPointerDown={() => { void warmResult(item); }} onFocus={() => { void warmResult(item); }} aria-label={title}>
        <span className="r3-po">
          {img && broken !== img ? <img key={img} src={img} alt="" loading="lazy" onError={() => setBroken(img)} />
            : <span className="r3-ph" data-testid="r3-placeholder" data-title={title} aria-hidden="true" />}
        </span>
        <span className="r3-ct">{title}</span>
        <span className="r3-cm">{metaR3(item)}</span>
      </button>
      {wl && (wl.inList ? (
        <span className="r3-wl" data-testid="r3-wl-in" role="img" aria-label={wl.tv ? `${title} is in your TV library` : `${title} is in your watchlist`}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
        </span>
      ) : (
        <button type="button" className="r3-wl" data-testid="r3-wl-add" aria-label={wl.tv ? `Add ${title} to your TV library` : `Add ${title} to watchlist`} onClick={wl.onAdd}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
        </button>
      ))}
    </div>
  );
}

/** The search modal: a centered dialog, 1040px wide at most with a 48px margin. At rest it is the field row
 * (R3_MODAL_REST tall); while searching it is R3_MODAL_OPEN tall and the results scroll inside it. */
export const R3_MODAL_W = 1040;
export const R3_MODAL_REST = 88;
export const R3_MODAL_OPEN = 760;
export const r3Css = `
[data-r3-search]{--r3-pad:clamp(32px, 3.4vw, 48px);--r3-top:clamp(300px, 29vw, 420px);--r3-po:clamp(112px, 9.5vw, 136px);color:rgba(255,255,255,.95);line-height:normal}
[data-r3-search]{--a1-dur:var(--m-dur-standard);--a1-ease:var(--m-ease-standard)}
/* Over the nav bar (z 56) and under the person view (z 60/70) and the detail sheets. */
.r3-modal{position:fixed;inset:0;z-index:58;display:flex;align-items:center;justify-content:center;padding:48px;box-sizing:border-box;
  transition:opacity var(--a1-dur) var(--a1-ease),visibility 0s linear var(--a1-dur)}
.r3-modal[data-open="true"]{opacity:1;visibility:visible;transition-delay:0s}
.r3-modal[data-open="false"]{opacity:0;visibility:hidden;pointer-events:none}
.r3-backdrop{position:absolute;inset:0;background:rgba(21,16,42,.84)}
.r3-panel{position:relative;width:min(${R3_MODAL_W}px, 100%);height:${R3_MODAL_REST}px;max-height:100%;display:flex;flex-direction:column;overflow:hidden;box-sizing:border-box;
  border-radius:28px;background:linear-gradient(180deg,rgba(167,139,250,.14) 0,rgba(167,139,250,0) 120px),rgba(21,16,42,.94);
  box-shadow:inset 0 0 0 1px rgba(196,181,253,.24),0 24px 60px rgba(8,5,24,.6);
  transition:height var(--a1-dur) var(--a1-ease),transform var(--a1-dur) var(--a1-ease)}
.r3-panel[data-searching="true"]{height:min(${R3_MODAL_OPEN}px, 100%)}
.r3-modal[data-open="false"] .r3-panel{transform:translateY(calc(var(--m-rise) * -1))}
.r3-head{flex:none;display:flex;align-items:center;gap:12px;height:${R3_MODAL_REST}px;box-sizing:border-box;padding:0 20px}
.r3-panel .r3-field{flex:1;width:auto;min-width:0}
.r3-close{flex:none;width:44px;height:44px;padding:0;border:0;border-radius:22px;display:grid;place-items:center;cursor:pointer;outline:none;
  color:#e2e2e9;background:rgba(255,255,255,.08)}
.r3-close:hover{background:rgba(255,255,255,.14)}
.r3-close:focus-visible{outline:2px solid #ede9fe;outline-offset:2px;box-shadow:0 0 0 4px rgba(21,16,42,.72),0 0 0 7px rgba(99,102,241,.78)}
@media (prefers-reduced-motion: reduce){[data-r3-search]{--a1-dur:0ms}}
/* v241 (H2): 480x48 pill, rgba(51,53,58,.85) fill, no border, filled 20px search icon, #c4c6d0 */
.r3-field{display:flex;align-items:center;gap:12px;width:480px;height:48px;box-sizing:border-box;padding:0 20px;border-radius:24px;
  background:rgba(51,53,58,.85);border:0;color:#c4c6d0;cursor:text;transition:box-shadow var(--m-dur-fast) var(--m-ease-standard)}
.r3-field:focus-within{box-shadow:0 0 0 2px var(--color-accent, #3b82f6)}
.r3-icon{display:inline-flex;color:#c4c6d0}
.r3-input{flex:1;min-width:0;background:none;border:0;outline:none;color:#e2e2e9;font:inherit;font-size:16px;caret-color:#e2e2e9}
.r3-input::placeholder{color:#c4c6d0;opacity:1}
.r3-input::-webkit-search-cancel-button{display:none}
.r3-body{flex:1;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:4px 24px 28px;box-sizing:border-box}
.r3-main{display:flex;gap:24px;align-items:flex-start;margin-bottom:28px}
.r3-top{width:var(--r3-top);flex:none;border-radius:16px;overflow:hidden;background:rgba(26,20,52,.88);border:1px solid rgba(255,255,255,.1);box-shadow:0 24px 64px rgba(0,0,0,.55)}
.r3-top-img{position:relative;height:calc(var(--r3-top) * 0.56);background:rgba(255,255,255,.06)}
.r3-top-img img{width:100%;height:100%;object-fit:cover;display:block}
.r3-top-text{padding:16px 18px 18px}
.r3-top-k{font-size:12px;font-weight:700;color:var(--color-accent-hover, #60a5fa)}
.r3-top-t{font-size:clamp(24px, 1.9vw, 28px);font-weight:800;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.r3-top-m{font-size:14px;color:rgba(255,255,255,.6);margin-top:2px}
.r3-btns{display:flex;gap:12px;margin-top:18px}
.r3-btn{height:44px;padding:0 22px;border-radius:999px;font:inherit;font-size:15px;font-weight:600;color:#fff;cursor:pointer;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.16);outline:none}
.r3-btn:hover{background:rgba(255,255,255,.18)}
.r3-primary{background:var(--color-accent, #3b82f6);border-color:var(--color-accent, #3b82f6)}
.r3-grid{flex:1;min-width:0;display:grid;grid-template-columns:repeat(auto-fill, var(--r3-po));gap:20px 16px;align-items:start}
.r3-cardw{position:relative;min-width:0;width:var(--r3-po);overflow:hidden}
/* v247 D3: chips and grid share one centered container (equal gutters); auto-fill columns fill it */
.d3{max-width:1600px;margin:0 auto}
.d3-chips{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:0 0 24px}
.d3-chip{height:36px;padding:0 16px;border-radius:8px;border:1px solid #44474e;background:none;color:#e2e2e9;font:inherit;font-size:14px;cursor:pointer;outline:none}
.d3-chip[aria-pressed="true"]{background:#3a4458;border-color:#3a4458;color:#d8e2ff}
.d3-chip:focus-visible{box-shadow:0 0 0 3px rgba(59,130,246,.45)}
.d3-grid{display:grid;grid-template-columns:repeat(auto-fill, minmax(min(160px, 100%), 1fr));gap:28px clamp(12px, 2.1vw, 30px);align-items:start}
.d3-grid .r3-cardw{width:100%}
.d3-grid .r3-ct{font-size:14px;font-weight:400;-webkit-line-clamp:1;line-clamp:1}
.d3-grid .r3-cm{font-size:12px;color:#c4c6d0}
.d3-person{display:flex;flex-direction:column;align-items:stretch;width:100%;padding:0;border:0;background:none;color:inherit;font:inherit;text-align:left;cursor:pointer;outline:none;min-width:0}
.d3-avatar{display:block;width:100%;aspect-ratio:1;border-radius:50%;overflow:hidden;background:rgba(255,255,255,.08)}
.d3-avatar img{width:100%;height:100%;object-fit:cover;display:block}
.d3-person:focus-visible .d3-avatar{box-shadow:0 0 0 3px rgba(59,130,246,.45)}
.r3-wl{position:absolute;top:8px;right:8px;width:36px;height:36px;border-radius:50%;display:grid;place-items:center;padding:0;cursor:pointer;color:#fff;
  background:rgba(21,16,42,.78);border:1px solid rgba(255,255,255,.16);outline:none}
.r3-tvadd{position:fixed;inset:0;z-index:60;display:grid;place-items:center;background:rgba(0,0,0,.5)}
.r3-tvadd-in{width:380px;max-width:calc(100vw - 32px);padding-top:16px;border-radius:16px;background:rgba(21,16,42,.96);border:1px solid rgba(255,255,255,.08)}
.r3-tvadd-t{padding:0 24px 12px;font-size:16px;font-weight:600;color:#fff}
.r3-wl:hover{background:rgba(59,130,246,.85)}
span.r3-wl{cursor:default;background:#22c55e;border-color:#22c55e}
.r3-wl:focus-visible{box-shadow:0 0 0 3px rgba(59,130,246,.45)}
.r3-card{display:flex;flex-direction:column;align-items:stretch;width:100%;box-sizing:border-box;padding:0;border:0;background:none;color:inherit;font:inherit;text-align:left;cursor:pointer;outline:none;min-width:0;border-radius:12px}
.r3-po{position:relative;display:block;width:100%;aspect-ratio:2/3;border-radius:12px;overflow:hidden;background:rgba(255,255,255,.06);box-shadow:0 8px 24px rgba(0,0,0,.4)}
.r3-po img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}
/* v244: no poster: a styled tile with the title (R3) */
.r3-ph{position:absolute;inset:0;display:flex;align-items:flex-end;padding:10px;box-sizing:border-box;font-size:15px;font-weight:800;line-height:1.15;
  color:rgba(255,255,255,.9);background:linear-gradient(160deg, rgba(59,130,246,.28), rgba(21,16,42,.9));overflow:hidden;word-break:break-word}
.r3-ph::after{content:attr(data-title);display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:5;overflow:hidden}
.r3-card:hover .r3-po img{filter:brightness(1.08)}
.r3-ct{font-size:13px;font-weight:600;margin-top:8px;line-height:1.3;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;overflow:hidden;overflow-wrap:anywhere}
.r3-cm{font-size:11px;color:rgba(255,255,255,.55);margin-top:2px}
.r3-badge{position:absolute;top:8px;right:8px;display:inline-flex;align-items:center;gap:5px;height:22px;padding:0 8px 0 4px;border-radius:999px;font-size:11px;font-weight:600;
  background:rgba(21,16,42,.78);border:1px solid rgba(255,255,255,.12);color:#fff}
.r3-badge i{width:14px;height:14px;border-radius:50%;background:#22c55e;display:grid;place-items:center;font-style:normal;font-size:9px}
.r3-sec{margin-bottom:24px}
.r3-sh{display:flex;align-items:baseline;gap:10px;margin:0 0 12px;font-size:18px;font-weight:700}
.r3-count{font-size:13px;font-weight:500;color:rgba(255,255,255,.5)}
.r3-people{display:flex;flex-wrap:wrap;gap:8px 20px}
.r3-person{width:132px;display:flex;flex-direction:column;align-items:center;padding:4px 0;border:0;background:none;color:inherit;font:inherit;cursor:pointer;outline:none;border-radius:12px;text-align:center}
.r3-avatar{width:72px;height:72px;border-radius:50%;overflow:hidden;background:rgba(255,255,255,.08);box-shadow:0 6px 18px rgba(0,0,0,.4)}
.r3-avatar img{width:100%;height:100%;object-fit:cover;display:block}
.r3-pn{font-size:14px;font-weight:600;margin-top:8px}
.r3-pr{font-size:12px;color:rgba(255,255,255,.55)}
.r3-more{display:flex;align-items:center;gap:12px;width:100%;height:52px;padding:0 18px;box-sizing:border-box;border-radius:14px;border:1px dashed rgba(255,255,255,.18);
  background:rgba(255,255,255,.03);color:#fff;font:inherit;cursor:pointer;outline:none}
.r3-more:hover{background:rgba(255,255,255,.06)}
.r3-more-t{font-size:15px;font-weight:600}
.r3-more-n{margin-left:auto;display:inline-flex;align-items:center;gap:4px;font-size:13px;color:rgba(255,255,255,.55)}
.r3-empty{padding:56px 0;text-align:center;font-size:15px;color:rgba(255,255,255,.7)}
.r3-sk{background:transparent;box-shadow:none}
.r3-card:focus-visible .r3-po,.r3-btn:focus-visible,.r3-more:focus-visible,.r3-person:focus-visible{box-shadow:0 0 0 3px rgba(59,130,246,.45)}
`;

/** v241 (H2): the filled search glyph from the H2 mock. */
function H2SearchIcon() {
  return (
    <svg width={20} height={20} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M15.5 14h-.8l-.3-.3A6.5 6.5 0 1 0 9.5 16a6.5 6.5 0 0 0 4.2-1.6l.3.3v.8l5 5 1.5-1.5-5-5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z" />
    </svg>
  );
}
