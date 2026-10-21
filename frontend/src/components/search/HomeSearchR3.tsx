/**
 * v227 R3 (Taylor's pick): desktop and tablet Home search (>= 1024px, the kiosk Home). A "Search" title and
 * field sit at the top of the page (not mid-hero). While searching, the hero shrinks to a short band and the
 * results show a large top-result card beside a mixed grid (movies and TV in your library), then People, then
 * "Find more for “q”", which opens the TMDb titles from the existing TMDb search path (performSearch's
 * /search?type=all; word-match filtered as C2). No key badges, no helper text. Phones keep their overlay.
 * Same data rules as the C2 palette: performSearch, 300 ms debounce, 2-character minimum, abort on retype.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { useModalHost } from '@/contexts/ModalHostContext';
import {
  performSearch, getPosterUrl, getProfileUrl, getTitle, getYear, TMDB_BUSY_TEXT,
  type SearchMovie, type SearchPerson, type SearchResults,
} from '@/services/searchApi';
import { backdropUrl, sizedPlexImg, tmdbImg } from '@/utils/image';
import { SearchIcon } from './MinimalSearchHost';
import { isMinimal } from '@/minimal';
import { fetchNativeWatchlistIdsOrNull } from '@/services/watchlistApi';
import { PathPicker } from '@/components/library/TvSearch';
import { loadTv, loadTvLibrary } from '@/components/library/tvLibraryApi';
import { addShow, type MonitorOption } from '@/components/library/tvCalendarApi';
import type { LibPath } from '@/components/library/libraryPathsApi';
import { WatchlistAddSheet } from '@/components/movies/WatchlistAddSheet';
import { img as homeImg, type Card } from '@/components/home/homeApi';
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
const backdropOf = (m: SearchMovie) => (m.backdrop_url ? tmdbImg(m.backdrop_url) : backdropUrl(m.backdrop_path, 'w780'));

export function HomeSearchR3({ recent = [], onOpenCard }: { recent?: Card[]; onOpenCard?: (c: Card) => void } = {}) {
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
    if (t) return tv ? { inList: tv.ids.has(t), onAdd: () => setTvAdd(m), tv: true } : undefined;
    const id = wlId(m);
    return wlIds && id ? { inList: wlIds.has(id), onAdd: () => setWlAdd(m) } : undefined;
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
  const [expanded, setExpanded] = useState(false);
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

  // '/' and the header's open-search focus the field (the C2 palette stands down while this is mounted)
  useEffect(() => {
    const onOpen = () => inputRef.current?.focus();
    window.addEventListener('open-search', onOpen);
    return () => window.removeEventListener('open-search', onOpen);
  }, []);
  useEffect(() => () => { abortRef.current?.abort(); if (debRef.current) clearTimeout(debRef.current); }, []);

  const searching = query.trim().length >= MIN_QUERY;
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('home-search', { detail: { searching } }));
  }, [searching]);
  const view = useMemo(() => buildR3(res, lastQ.current), [res]);

  const open = (item: SearchMovie) => {
    const tmdbId = item.tmdb_id || item.id;
    const opts = { ratingKey: (item.isOwned && item.rating_key) ? item.rating_key : undefined, tmdbId: tmdbId ?? undefined, origin: 'search' as const };
    if (!opts.ratingKey && !opts.tmdbId) return;
    if (item.media_type === 'tv') openTv(opts); else openMovie(opts);
  };
  const openPerson = (p: SearchPerson) =>
    window.dispatchEvent(new CustomEvent('search-query', { detail: { query: p.name, tab: 'people', personId: p.id } }));

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') { e.preventDefault(); if (query) clear(); else inputRef.current?.blur(); }
    else if (e.key === 'Enter' && phase === 'done' && view.top) { e.preventDefault(); open(view.top); }
  };

  const q = lastQ.current;
  const nothing = phase === 'done' && !view.top && view.people.length === 0;
  return (
    <div data-r3-search data-testid="r3-search" data-searching={searching ? 'true' : 'false'} style={{ fontFamily: FONT }}>
      <style>{r3Css}</style>
      {/* v228 (PM, R3-rest): the hero is a short band at rest too */}
      <div className="r3-cover" data-testid="r3-cover" aria-hidden="true" />
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
      <div className="r3-head">
        <h1 className="r3-title">Search</h1>
        <label className="r3-field" data-focus-ring>
          <span className="r3-icon"><SearchIcon size={20} /></span>
          <input ref={inputRef} className="r3-input" data-testid="search-input" type="search" placeholder="Search" aria-label="Search"
            value={query} autoComplete="off" spellCheck={false} onKeyDown={onKey}
            onChange={e => {
              const v = e.target.value; setQuery(v);
              if (debRef.current) clearTimeout(debRef.current);
              debRef.current = setTimeout(() => void run(v.trim()), DEBOUNCE_MS);
            }} />
        </label>
      </div>
      {!searching && recent.length > 0 && (
        <div className="r3-body" data-testid="r3-rest">
          <section className="r3-sec">
            <h2 className="r3-sh">Recently added</h2>
            <div className="r3-grid r3-grid-rest">
              {recent.map((c, i) => (
                <button key={`${c.type}-${c.key}-${i}`} type="button" className="r3-card" data-testid="r3-recent" aria-label={c.title} onClick={() => onOpenCard?.(c)}>
                  <span className="r3-po">{c.poster && <img src={homeImg(c.poster) ?? undefined} alt="" loading="lazy" />}<InLibrary /></span>
                  <span className="r3-ct">{c.title}</span>
                  {c.subtitle && <span className="r3-cm">{c.subtitle}</span>}
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
      {searching && (
        <div className="r3-body" data-testid="r3-results" data-state={phase === 'loading' ? (skeleton ? 'loading' : 'pending') : nothing ? 'empty' : phase}>
          {phase === 'loading' && skeleton && (
            <div className="r3-main" data-testid="search-skeleton" aria-hidden="true">
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
          {phase === 'done' && view.top && (
            <div className="r3-main" data-testid="r3-main">
              <TopCard item={view.top} onOpen={() => open(view.top!)} />
              <div className="r3-grid" data-testid="r3-grid">
                {view.grid.map((m, i) => <PosterCard key={`${m.media_type}-${m.rating_key ?? m.id}-${i}`} item={m} onOpen={() => open(m)} wl={wlFor(m)} />)}
              </div>
            </div>
          )}
          {phase === 'done' && view.people.length > 0 && (
            <section className="r3-sec" data-testid="r3-people">
              <h2 className="r3-sh">People <span className="r3-count">{view.people.length}</span></h2>
              <div className="r3-people">
                {view.people.map(p => {
                  const img = getProfileUrl(p);
                  return (
                    <button key={`${p.id}-${p.name}`} type="button" className="r3-person" data-testid="r3-person" onClick={() => openPerson(p)}>
                      <span className="r3-avatar">{img && <img src={img} alt="" />}</span>
                      <span className="r3-pn">{p.name}</span>
                      <span className="r3-pr">{p.role || p.known_for_department || ''}</span>
                    </button>
                  );
                })}
              </div>
            </section>
          )}
          {phase === 'done' && view.more.length > 0 && !expanded && (
            <button type="button" className="r3-more" data-testid="find-more" onClick={() => setExpanded(true)}>
              <span className="r3-icon"><SearchIcon size={18} /></span>
              <span className="r3-more-t">{`Find more for “${q}”`}</span>
              <span className="r3-more-n">{`${view.more.length} on TMDb`}<Chevron /></span>
            </button>
          )}
          {phase === 'done' && expanded && (
            <section className="r3-sec" data-testid="r3-tmdb">
              <h2 className="r3-sh">On TMDb <span className="r3-count">{view.more.length}</span></h2>
              <div className="r3-grid r3-grid-wide">
                {view.more.map((m, i) => <PosterCard key={`t-${m.media_type}-${m.id}-${i}`} item={m} onOpen={() => open(m)} wl={wlFor(m)} />)}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function InLibrary() {
  return <span className="r3-badge" data-testid="r3-in-library"><i aria-hidden="true">✓</i>In library</span>;
}

function TopCard({ item, onOpen }: { item: SearchMovie; onOpen: () => void }) {
  const img = backdropOf(item) || getPosterUrl(item);
  return (
    <article className="r3-top" data-testid="r3-top">
      <div className="r3-top-img">
        {img && <img src={img} alt="" />}
        {item.isOwned && <InLibrary />}
      </div>
      <div className="r3-top-text">
        <div className="r3-top-k">Top result</div>
        <div className="r3-top-t" data-testid="r3-top-title">{getTitle(item)}</div>
        <div className="r3-top-m">{metaR3(item)}</div>
        <div className="r3-btns">
          <button type="button" className="r3-btn" data-testid="r3-details" onClick={onOpen}>Details</button>
        </div>
      </div>
    </article>
  );
}

function PosterCard({ item, onOpen, wl }: { item: SearchMovie; onOpen: () => void; wl?: { inList: boolean; onAdd: () => void; tv?: boolean } }) {
  const img = sizedPlexImg(getPosterUrl(item), 272, 408);
  const title = getTitle(item);
  return (
    <div className="r3-cardw">
      <button type="button" className="r3-card" data-testid="r3-card" data-owned={item.isOwned ? 'true' : 'false'} onClick={onOpen} aria-label={title}>
        <span className="r3-po">
          {img && <img src={img} alt="" loading="lazy" />}
          {item.isOwned && <InLibrary />}
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

function Chevron() {
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>;
}

/** Layout: 48px page gutter at 1440 (32px at 1024); the field is 640px (520px at 1024); the band is 200px. */
export const R3_BAND = 200;
export const r3Css = `
[data-r3-search]{--r3-pad:clamp(32px, 3.4vw, 48px);--r3-top:clamp(300px, 29vw, 420px);--r3-po:clamp(112px, 9.5vw, 136px);--r3-rest-po:clamp(128px, 10.4vw, 150px);color:rgba(255,255,255,.95);line-height:normal}
.r3-cover{position:fixed;left:0;right:0;top:0;bottom:0;z-index:40;pointer-events:none;
  background:linear-gradient(to bottom, transparent 0, rgba(21,16,42,.55) ${R3_BAND - 80}px, #15102a ${R3_BAND}px, #15102a 100%)}
.r3-head{position:fixed;z-index:55;top:104px;left:var(--r3-pad);right:var(--r3-pad);display:flex;align-items:center;gap:clamp(20px, 2vw, 28px)}
.r3-title{margin:0;font-size:clamp(28px, 2.2vw, 32px);font-weight:800;letter-spacing:-.01em;color:#fff}
.r3-field{display:flex;align-items:center;gap:12px;width:clamp(520px, 44vw, 640px);height:48px;box-sizing:border-box;padding:0 18px;border-radius:999px;
  background:rgba(21,16,42,.62);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border:1px solid rgba(255,255,255,.14);cursor:text;
  transition:border-color 150ms,box-shadow 150ms}
.r3-field:focus-within{border-color:var(--color-accent, #3b82f6);box-shadow:0 0 0 3px rgba(59,130,246,.28);background:rgba(21,16,42,.75)}
.r3-icon{display:inline-flex;color:var(--color-accent-hover, #60a5fa)}
.r3-input{flex:1;min-width:0;background:none;border:0;outline:none;color:#fff;font:inherit;font-size:16px;caret-color:var(--color-accent-hover, #60a5fa)}
.r3-input::placeholder{color:rgba(255,255,255,.55)}
.r3-input::-webkit-search-cancel-button{display:none}
.r3-body{position:fixed;z-index:45;top:${R3_BAND}px;left:0;right:0;bottom:0;overflow-y:auto;overscroll-behavior:contain;padding:8px var(--r3-pad) 40px;box-sizing:border-box}
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
.r3-grid{flex:1;min-width:0;display:grid;grid-template-columns:repeat(auto-fill, var(--r3-po));gap:20px 16px}
.r3-cardw{position:relative;min-width:0}
.r3-wl{position:absolute;top:8px;right:8px;width:36px;height:36px;border-radius:50%;display:grid;place-items:center;padding:0;cursor:pointer;color:#fff;
  background:rgba(21,16,42,.78);border:1px solid rgba(255,255,255,.16);outline:none}
.r3-tvadd{position:fixed;inset:0;z-index:60;display:grid;place-items:center;background:rgba(0,0,0,.5)}
.r3-tvadd-in{width:380px;max-width:calc(100vw - 32px);padding-top:16px;border-radius:16px;background:rgba(21,16,42,.96);border:1px solid rgba(255,255,255,.08)}
.r3-tvadd-t{padding:0 24px 12px;font-size:16px;font-weight:600;color:#fff}
.r3-wl:hover{background:rgba(59,130,246,.85)}
span.r3-wl{cursor:default;background:#22c55e;border-color:#22c55e}
.r3-wl:focus-visible{box-shadow:0 0 0 3px rgba(59,130,246,.45)}
.r3-grid-rest{grid-template-columns:repeat(auto-fill, var(--r3-rest-po))}
.r3-card{display:flex;flex-direction:column;align-items:stretch;padding:0;border:0;background:none;color:inherit;font:inherit;text-align:left;cursor:pointer;outline:none;min-width:0;border-radius:12px}
.r3-po{position:relative;display:block;aspect-ratio:2/3;border-radius:12px;overflow:hidden;background:rgba(255,255,255,.06);box-shadow:0 8px 24px rgba(0,0,0,.4)}
.r3-po img{width:100%;height:100%;object-fit:cover;display:block}
.r3-card:hover .r3-po img{filter:brightness(1.08)}
.r3-ct{font-size:13px;font-weight:600;margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
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
.r3-sk{background:rgba(255,255,255,.06);animation:r3-pulse 1.2s ease-in-out infinite;box-shadow:none}
.r3-card:focus-visible .r3-po,.r3-btn:focus-visible,.r3-more:focus-visible,.r3-person:focus-visible{box-shadow:0 0 0 3px rgba(59,130,246,.45)}
@keyframes r3-pulse{0%,100%{opacity:1}50%{opacity:.55}}
@media (prefers-reduced-motion: reduce){.r3-sk{animation:none}}
`;
