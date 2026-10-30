/**
 * v215 C2: desktop search palette (>= 1024px) off the kiosk Home (v227 R3 has its own field there).
 * Opens from '/' or `open-search`; v228: closing returns focus to whatever had it before. Same data as KioskSearch
 * (performSearch, 300 ms debounce, 2-char minimum, abort on retype).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { useModalHost } from '@/contexts/ModalHostContext';
import {
  openResult, warmResult, warmResults,
  performSearch, getPosterUrl, getProfileUrl, getTitle, getYear, TMDB_BUSY_TEXT,
  type SearchMovie, type SearchPerson, type SearchResults,
} from '@/services/searchApi';
import { sizedPlexImg } from '@/utils/image';
import { SearchIcon } from './MinimalSearchHost';
import { wordMatchRatio, MIN_MATCH_RATIO } from './KioskSearch';

export type Chip = 'all' | 'movies' | 'tv' | 'people';
const CHIPS: { id: Chip; label: string }[] = [
  { id: 'all', label: 'All' }, { id: 'movies', label: 'Movies' }, { id: 'tv', label: 'TV' }, { id: 'people', label: 'People' },
];
export const MIN_QUERY = 2;
export const DEBOUNCE_MS = 300;
export const SKELETON_DELAY_MS = 150;
export const CLOSE_MS = 140;

export type Row =
  | { kind: 'media'; item: SearchMovie; owned: boolean }
  | { kind: 'person'; item: SearchPerson };
export type Group = { id: 'library' | 'people' | 'more'; title: string; rows: Row[] };

/** Prod 1b4ea74 KioskSearch: TMDb titles (word-match filtered) hide behind a collapsed "Find More" button. */
export function unownedRows(res: SearchResults | null, chip: Chip, q: string): Row[] {
  if (!res || chip === 'people') return [];
  return res.unowned.filter(m => mediaFor(chip)(m)).filter(m => wordMatchRatio(q, getTitle(m)) >= MIN_MATCH_RATIO)
    .map((item): Row => ({ kind: 'media', item, owned: false }));
}
const mediaFor = (chip: Chip) => (m: SearchMovie) =>
  chip === 'all' || (chip === 'movies' ? m.media_type !== 'tv' : chip === 'tv' ? m.media_type === 'tv' : false);

/** Prod: on Movies/TV with nothing owned, Find More opens by itself (never on All). */
export function autoExpand(res: SearchResults | null, chip: Chip): boolean {
  return !!res && (chip === 'movies' || chip === 'tv') && res.owned.filter(mediaFor(chip)).length === 0
    && res.unowned.filter(mediaFor(chip)).length > 0;
}

/** Collapsed: In your library + People (Find More button below). Expanded: the TMDb titles only, as prod. */
export function buildGroups(res: SearchResults | null, chip: Chip, q: string, expanded = false): Group[] {
  if (!res) return [];
  if (expanded) {
    const more = unownedRows(res, chip, q);
    return more.length ? [{ id: 'more', title: 'Find More', rows: more }] : [];
  }
  const owned = res.owned.filter(mediaFor(chip)).map((item): Row => ({ kind: 'media', item, owned: true }));
  const people = chip === 'all' || chip === 'people'
    ? [...res.ownedPeople, ...res.unownedPeople].map((item): Row => ({ kind: 'person', item })) : [];
  return ([
    { id: 'library', title: 'In your library', rows: owned },
    { id: 'people', title: 'People', rows: people },
  ] as Group[]).filter(g => g.rows.length > 0);
}

export function metaLine(r: Row): string {
  if (r.kind === 'person') return r.item.known_for_department || r.item.role || '';
  const y = getYear(r.item);
  const k = r.item.media_type === 'tv' ? 'TV' : 'Movie';
  return y ? `${y} · ${k}` : k;
}

/** Title with every case-insensitive match of the query in accent blue. */
export function Highlight({ text, q }: { text: string; q: string }) {
  const n = q.trim().toLowerCase();
  if (!n) return <>{text}</>;
  const out: React.ReactNode[] = [];
  const low = text.toLowerCase();
  let i = 0;
  for (let j = low.indexOf(n); j >= 0; j = low.indexOf(n, i)) {
    if (j > i) out.push(text.slice(i, j));
    out.push(<em key={j} className="lsp-em">{text.slice(j, j + n.length)}</em>);
    i = j + n.length;
  }
  if (i < text.length) out.push(text.slice(i));
  return <>{out}</>;
}

/** A focusable control other than the search input: it handles its own Enter/Space. */
export function isControl(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el?.closest?.('button, a[href], select, textarea, [role="button"], [role="tab"], [role="checkbox"]');
}

type Phase = 'idle' | 'loading' | 'done' | 'error';

export function HomeSearchPalette() {
  const { openMovie, openTv } = useModalHost();
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const [query, setQuery] = useState('');
  const [chip, setChip] = useState<Chip>('all');
  const [res, setRes] = useState<SearchResults | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [skeleton, setSkeleton] = useState(false);
  const [hi, setHi] = useState(0);
  const [expanded, setExpanded] = useState(false);
  // Tester: toggling Find More unmounts the pressed button; focus moves to the button that replaces it.
  const focusAfter = useRef<'find-more' | 'find-more-back' | null>(null);
  const toggleMore = (open: boolean) => { focusAfter.current = open ? 'find-more-back' : 'find-more'; setExpanded(open); };
  useEffect(() => {
    const id = focusAfter.current;
    if (!id) return;
    focusAfter.current = null;
    panelRef.current?.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.focus();
  }, [expanded]);
  const inputRef = useRef<HTMLInputElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const debRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const skelRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastQ = useRef('');
  const chipRef = useRef<Chip>('all');
  chipRef.current = chip;

  const run = useCallback(async (q: string) => {
    abortRef.current?.abort();
    if (skelRef.current) clearTimeout(skelRef.current);
    lastQ.current = q;
    if (q.length < MIN_QUERY) { setPhase('idle'); setRes(null); setSkeleton(false); return; }
    const ctl = new AbortController();
    abortRef.current = ctl;
    setPhase('loading');
    setSkeleton(false);
    skelRef.current = setTimeout(() => setSkeleton(true), SKELETON_DELAY_MS);
    try {
      const r = await performSearch(q, ctl.signal);
      if (ctl.signal.aborted) return;
      setRes(r); setHi(0); setExpanded(autoExpand(r, chipRef.current)); setPhase(r.failed ? 'error' : 'done');
    } catch (e) {
      if ((e as { name?: string })?.name === 'AbortError' || ctl.signal.aborted) return;
      setPhase('error');
    } finally {
      if (skelRef.current) clearTimeout(skelRef.current);
      if (!ctl.signal.aborted) setSkeleton(false);
    }
  }, []);

  const close = useCallback(() => {
    abortRef.current?.abort();
    if (debRef.current) clearTimeout(debRef.current);
    setClosing(true);
    window.setTimeout(() => {
      setOpen(false); setClosing(false);
      window.dispatchEvent(new CustomEvent('search-palette', { detail: { open: false } }));
      const back = returnTo.current; returnTo.current = null;
      requestAnimationFrame(() => requestAnimationFrame(() => back?.focus?.()));
    }, CLOSE_MS);
  }, []);

  useEffect(() => {
    const onOpen = () => {
      // v227: on the kiosk Home, R3's own field takes '/' and open-search
      if (document.querySelector('[data-r3-search]')) return;
      returnTo.current = document.activeElement as HTMLElement | null;
      setClosing(false); setOpen(true); setQuery(''); setRes(null); setPhase('idle'); setChip('all'); setHi(0);
      window.dispatchEvent(new CustomEvent('search-palette', { detail: { open: true } }));
      setTimeout(() => inputRef.current?.focus(), 0);
    };
    window.addEventListener('open-search', onOpen);
    return () => window.removeEventListener('open-search', onOpen);
  }, []);

  const groups = useMemo(() => buildGroups(res, chip, lastQ.current, expanded), [res, chip, expanded]);
  const moreCount = useMemo(() => unownedRows(res, chip, lastQ.current).length, [res, chip]);
  const rows = useMemo(() => groups.flatMap(g => g.rows), [groups]);
  useEffect(() => { setHi(0); }, [chip, expanded]);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-row="${hi}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [hi]);

  const choose = (r: Row, el?: HTMLElement | null) => {
    if (r.kind === 'person') {
      close();
      window.dispatchEvent(new CustomEvent('search-query', { detail: { query: r.item.name, tab: 'people', personId: r.item.id } }));
      return;
    }
    const item = r.item;
    const tmdbId = item.tmdb_id || item.id;
    const opts = { ratingKey: (item.isOwned && item.rating_key) ? item.rating_key : undefined, tmdbId: tmdbId ?? undefined, origin: 'search' as const };
    if (!opts.ratingKey && !opts.tmdbId) { close(); return; }
    // Same open as a Home card tap (openWhenReady through openResult); the palette closes as the popup opens.
    void openResult(item, el ?? listRef.current?.querySelector<HTMLElement>(`[data-row="${hi}"]`), () => {
      close();
      if (item.media_type === 'tv') openTv(opts); else openMovie(opts);
    });
  };
  useEffect(() => {
    if (!res) return;
    return warmResults([...res.owned, ...res.unowned]);
  }, [res]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return; }
    if (e.key === 'Tab') {
      const f = [...(panelRef.current?.querySelectorAll<HTMLElement>('input,button') ?? [])];
      if (!f.length) return;
      const i = f.indexOf(document.activeElement as HTMLElement);
      const n = e.shiftKey ? (i <= 0 ? f.length - 1 : i - 1) : (i === f.length - 1 ? 0 : i + 1);
      e.preventDefault(); f[n]?.focus(); return;
    }
    // Tester MED: Enter/Space on a focused control (Find More, ‹ Find More, Try again, chips) is that control's own click.
    if ((e.key === 'Enter' || e.key === ' ') && isControl(e.target)) return;
    if (!rows.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi(h => Math.min(rows.length - 1, h + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi(h => Math.max(0, h - 1)); }
    else if (e.key === 'Home' && e.target !== inputRef.current) { e.preventDefault(); setHi(0); }
    else if (e.key === 'End' && e.target !== inputRef.current) { e.preventDefault(); setHi(rows.length - 1); }
    else if (e.key === 'Enter' && phase === 'done') { e.preventDefault(); const r = rows[hi]; if (r) choose(r); }
  };

  if (!open) return null;
  const showChips = query.trim().length >= MIN_QUERY && phase !== 'error';
  let idx = -1;
  return (
    <div className="lsp-root" data-closing={closing ? 'true' : 'false'} style={{ fontFamily: FONT }} onKeyDown={onKey}>
      <style>{paletteCss}</style>
      <div className="lsp-scrim" data-testid="search-scrim" onMouseDown={close} />
      <div ref={panelRef} className="lsp-panel" role="dialog" aria-modal="true" aria-label="Search" data-testid="search-palette"
        data-state={phase === 'loading' ? (skeleton ? 'loading' : 'pending') : phase === 'done' && rows.length === 0 ? 'empty' : phase}>
        <div className="lsp-input-row">
          <span className="lsp-icon"><SearchIcon size={22} /></span>
          <input ref={inputRef} className="lsp-input" placeholder="Search" value={query} role="combobox"
            aria-expanded={rows.length > 0} aria-controls="lsp-list" aria-autocomplete="list"
            aria-activedescendant={rows.length && phase === 'done' ? `lsp-row-${hi}` : undefined}
            autoComplete="off" spellCheck={false} data-testid="search-input"
            onChange={e => {
              const v = e.target.value; setQuery(v);
              if (debRef.current) clearTimeout(debRef.current);
              debRef.current = setTimeout(() => void run(v.trim()), DEBOUNCE_MS);
            }} />
        </div>
        {showChips && (
          <div className="lsp-chips" role="tablist">
            {CHIPS.map(c => (
              <button key={c.id} type="button" role="tab" aria-selected={chip === c.id} className="lsp-chip"
                data-on={chip === c.id ? 'true' : 'false'} onClick={() => { setChip(c.id); setExpanded(autoExpand(res, c.id)); }}>{c.label}</button>
            ))}
          </div>
        )}
        {phase === 'loading' && skeleton && (
          <div className="lsp-list" data-testid="search-skeleton" aria-hidden="true">
            <div className="lsp-sk lsp-sk-h" />
            {[180, 240, 150, 210].map(w => (
              <div key={w} className="lsp-row"><div className="lsp-th lsp-sk2" />
                <div className="lsp-rt"><div className="lsp-sk lsp-sk2" style={{ width: w, height: 12, borderRadius: 6 }} />
                  <div className="lsp-sk" style={{ width: 90, height: 10, borderRadius: 5, marginTop: 8 }} /></div></div>
            ))}
          </div>
        )}
        {phase === 'done' && rows.length === 0 && moreCount === 0 && (
          <div className="lsp-empty" data-testid="search-empty">{res?.rateLimited ? TMDB_BUSY_TEXT : 'No results'}</div>
        )}
        {phase === 'error' && (
          <div className="lsp-error" data-testid="search-error">
            <div className="lsp-error-t">Couldn't search</div>
            <button type="button" className="lsp-retry" onClick={() => void run(lastQ.current)}>Try again</button>
          </div>
        )}
        {phase === 'done' && (rows.length > 0 || moreCount > 0) && (
          <div ref={listRef} className="lsp-list lsp-scroll" id="lsp-list" role="listbox" data-testid="search-results">
            {groups.map(g => (
              <div key={g.id} role="group" aria-label={g.title}>
                {g.id === 'more'
                  ? <button type="button" className="lsp-fm-back" data-testid="find-more-back" onClick={() => toggleMore(false)}><ChevronLeft />Find More</button>
                  : <div className="lsp-ghd" data-testid={`group-${g.id}`}>{g.title}</div>}
                {g.rows.map(r => {
                  idx += 1; const i = idx;
                  const title = r.kind === 'person' ? r.item.name : getTitle(r.item);
                  const img = r.kind === 'person' ? getProfileUrl(r.item) : sizedPlexImg(getPosterUrl(r.item), 68, 100);
                  return (
                    <div key={i} id={`lsp-row-${i}`} data-row={i} role="option" aria-selected={i === hi}
                      className="lsp-row" data-hi={i === hi ? 'true' : 'false'}
                      onMouseEnter={() => { setHi(i); if (r.kind !== 'person') void warmResult(r.item); }} onClick={(e) => choose(r, e.currentTarget)}>
                      {r.kind === 'person'
                        ? <div className="lsp-pp">{img && <img src={img} alt="" />}</div>
                        : <div className="lsp-th">{img && <img src={img} alt="" />}</div>}
                      <div className="lsp-rt">
                        <div className="lsp-a"><Highlight text={title} q={lastQ.current} /></div>
                        <div className="lsp-b">{metaLine(r)}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            ))}
            {!expanded && moreCount > 0 && (
              <button type="button" className="lsp-fm" data-testid="find-more" onClick={() => toggleMore(true)}>Find More<ChevronDown /></button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Prod 1b4ea74's Find More chevrons, as is. */
function ChevronDown() {
  return <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>;
}
function ChevronLeft() {
  return <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6" /></svg>;
}

/** v228: the C2 pill is gone; the palette (off Home) keeps its old top, clamp(184px, 31vh, 372px). */
const PAL_TOP = 'min(clamp(184px, 31vh, 372px), calc(100vh - 24px - 420px))';
export const paletteCss = `
.lsp-root{position:fixed;inset:0;z-index:200;color:rgba(255,255,255,.95);line-height:normal}
.lsp-scrim{position:absolute;inset:0;background:rgba(21,16,42,.72);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);animation:lsp-fade 180ms ease-out}
.lsp-panel{position:absolute;left:50%;top:max(24px, ${PAL_TOP});transform:translateX(-50%);width:min(720px, calc(100vw - 96px));
  max-height:calc(100vh - max(24px, ${PAL_TOP}) - 24px);display:flex;flex-direction:column;overflow:hidden;box-sizing:border-box;
  background:rgba(21,16,42,.92);backdrop-filter:blur(40px);-webkit-backdrop-filter:blur(40px);border:1px solid var(--color-glass-8);
  border-radius:14px;box-shadow:0 24px 60px rgba(21,16,42,.6);animation:lsp-in 200ms cubic-bezier(.2,.8,.2,1)}
.lsp-input-row{flex:none;display:flex;align-items:center;gap:14px;height:64px;padding:0 22px;border-bottom:1px solid var(--color-glass-8)}
.lsp-icon{display:inline-flex;color:var(--color-accent-hover)}
.lsp-input{flex:1;min-width:0;background:none;border:0;outline:none;color:rgba(255,255,255,.95);font:inherit;font-size:20px;caret-color:var(--color-accent-hover)}
.lsp-input::placeholder{color:rgba(255,255,255,.50)}
.lsp-key{font-size:12px;font-weight:500;color:rgba(255,255,255,.70);border:1px solid var(--color-glass-10);border-radius:6px;padding:1px 6px}
.lsp-chips{flex:none;display:flex;gap:8px;padding:10px 16px 4px}
.lsp-chip{font:inherit;font-size:13px;padding:6px 14px;border-radius:999px;background:var(--color-glass-6);border:1px solid var(--color-glass-8);color:rgba(255,255,255,.95);cursor:pointer;outline:none}
.lsp-chip[data-on="true"]{background:var(--color-glass-10);color:#fff}
.lsp-chip:focus-visible,.lsp-retry:focus-visible{box-shadow:0 0 0 3px rgba(59,130,246,.28);border-color:var(--color-accent)}
.lsp-list{padding:6px 8px 10px}
.lsp-scroll{overflow-y:auto;overscroll-behavior:contain;min-height:0}
.lsp-ghd{font-size:12px;font-weight:600;color:rgba(255,255,255,.50);padding:10px 12px 6px}
.lsp-row{display:flex;align-items:center;gap:14px;padding:8px 12px;border-radius:10px;min-height:64px;box-sizing:border-box;cursor:pointer}
.lsp-row[data-hi="true"]{background:rgba(59,130,246,.18);box-shadow:inset 0 0 0 1px rgba(96,165,250,.35)}
.lsp-th{width:34px;height:50px;border-radius:6px;flex:none;border:1px solid var(--color-glass-8);overflow:hidden;background:var(--color-glass-6);box-sizing:border-box}
.lsp-pp{width:44px;height:44px;border-radius:22px;flex:none;overflow:hidden;background:var(--color-glass-6)}
.lsp-th img,.lsp-pp img{width:100%;height:100%;object-fit:cover;display:block}
.lsp-rt{flex:1;min-width:0}
.lsp-a{font-size:14px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lsp-em{font-style:normal;font-weight:inherit;color:var(--color-accent-hover)}
.lsp-b{font-size:12px;color:rgba(255,255,255,.50);margin-top:3px}
.lsp-sk{background:var(--color-glass-6);animation:lsp-pulse 1.2s ease-in-out infinite}
.lsp-sk2{background:var(--color-glass-10)}
.lsp-sk-h{height:12px;width:110px;border-radius:6px;margin:12px}
.lsp-th.lsp-sk2{border-color:var(--color-glass-8);animation:lsp-pulse 1.2s ease-in-out infinite}
.lsp-fm{display:flex;width:100%;align-items:center;justify-content:center;gap:8px;padding:12px 0;background:none;border:0;font:inherit;font-size:18px;font-weight:500;color:rgba(255,255,255,.80);cursor:pointer;transition:color 150ms;outline:none;border-radius:10px}
.lsp-fm:hover{color:#fff}
.lsp-fm-back{display:flex;width:100%;align-items:center;justify-content:center;gap:8px;margin:6px 0 12px;padding:0;background:none;border:0;font:inherit;font-size:18px;font-weight:600;color:var(--color-text-secondary, rgba(255,255,255,.70));cursor:pointer;transition:color 150ms;outline:none;border-radius:10px}
.lsp-fm-back:hover{color:var(--color-text-primary, rgba(255,255,255,.95))}
.lsp-fm:focus-visible,.lsp-fm-back:focus-visible{box-shadow:0 0 0 3px rgba(59,130,246,.28)}
.lsp-empty{padding:44px 0 52px;text-align:center;font-size:15px;color:rgba(255,255,255,.70)}
.lsp-error{padding:40px 0 44px;text-align:center}
.lsp-error-t{font-size:15px;color:rgba(255,255,255,.95)}
.lsp-retry{display:inline-flex;align-items:center;height:44px;margin-top:16px;padding:0 22px;border-radius:999px;border:1px solid transparent;
  background:var(--color-accent);color:#fff;font:inherit;font-size:14px;font-weight:500;cursor:pointer;outline:none}
.lsp-root[data-closing="true"] .lsp-scrim{animation:lsp-fade-out 140ms ease-in forwards}
.lsp-root[data-closing="true"] .lsp-panel{animation:lsp-out 140ms ease-in forwards}
@keyframes lsp-fade-out{from{opacity:1}to{opacity:0}}
@keyframes lsp-out{from{opacity:1;transform:translateX(-50%)}to{opacity:0;transform:translateX(-50%) translateY(-4px) scale(.98)}}
@keyframes lsp-fade{from{opacity:0}to{opacity:1}}
@keyframes lsp-in{from{opacity:0;transform:translateX(-50%) translateY(-4px) scale(.98)}to{opacity:1;transform:translateX(-50%)}}
@keyframes lsp-pulse{0%,100%{opacity:1}50%{opacity:.55}}
@keyframes lsp-in-rm{from{opacity:0}to{opacity:1}}
@media (prefers-reduced-motion: reduce){
  .lsp-scrim{animation:lsp-fade 100ms linear}
  .lsp-panel{animation:lsp-in-rm 100ms linear}
  .lsp-sk,.lsp-th.lsp-sk2{animation:none}
  .lsp-root[data-closing="true"] .lsp-scrim,.lsp-root[data-closing="true"] .lsp-panel{animation:lsp-fade-out 100ms linear forwards}
}
`;
