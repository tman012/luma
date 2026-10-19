/** Settings > TV > Series (desktop web): every library show as a 2:3 poster,
 * A–Z ignoring punctuation and a leading The/A/An, with a downloaded-share bar and "N of M
 * episodes", and a sticky # – Z rail. A poster opens Edit series (v158a); Select picks shows
 * for the bulk Missing episodes opt-in. v181: a Posters/List toggle; the list view is the Sonarr-style bulk editor
 * (TvBulk: a checkbox per show, select all, the action bar) and a title opens the show sheet (episode select). */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { EditSeriesModal } from './TvEdit';
import { loadProfiles, monitorMissing, type Profile } from './tvCalendarApi';
import { BulkBar, SeriesList, ShowSheet } from './TvBulk';
import { FONT } from '@/components/start/StartUI';
import { posterUrl } from '@/utils/image';
import { RATING_ICONS } from '@/utils/ratingIcons';
import type { Show } from './tvLibraryApi';

const W50 = 'rgba(255,255,255,0.5)';
const W95 = 'rgba(255,255,255,0.95)';
const ACCENT = '#3b82f6';
const RAIL_TOP = 24;
/** The A–Z rail: 13/600 letters spread evenly down the full available height (from under the header to the
 * viewport bottom), 24px targets, a glass pill on hover, the focus ring on keyboard focus. */
export const RAIL = { width: 32, target: 24, font: 13, bottom: 16 };
const RAIL_MIN_H = 27 * RAIL.target;               // # + A–Z
const railHeight = (top: number) => Math.max(RAIL_MIN_H, Math.round(window.innerHeight - Math.max(top, RAIL_TOP) - RAIL.bottom));
export const tvSeriesCss = `
.tvs-rail-btn{border-radius:999px;transition:background-color .12s,color .12s}
.tvs-rail-btn[aria-disabled=false]:hover{color:rgba(255,255,255,.95)!important;background:rgba(255,255,255,.10)!important}
.tvs-glass:hover{background:rgba(255,255,255,.10)!important}
.tvs-poster:hover img{filter:brightness(1.08)}
`;
export const LETTERS = ['#', ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'];

/** The sort key (mirrors tv_scan.sort_title): accents folded, lower case, periods and
 * apostrophes dropped (S.W.A.T. -> swat), other symbols as spaces, a leading The/A/An ignored. */
export const sortKey = (title: string) => {
  const t = (title || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[.'\u2019\u2018`]/g, '').replace(/[^0-9a-z]+/g, ' ').trim();
  return t.replace(/^(?:the|a|an) (?=\S)/, '') || t;
};
const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
export const letterOf = (title: string) => {
  const c = sortKey(title).charAt(0).toUpperCase();
  return c >= 'A' && c <= 'Z' ? c : '#';
};
export const sortShows = (shows: Show[]) =>
  [...shows].sort((a, b) => cmp(sortKey(a.title), sortKey(b.title)) || (a.year ?? 0) - (b.year ?? 0));

/** Bar color: red when aired episodes are missing, blue when it continues with none missing, green when complete. */
export function progress(s: Show): { fill: number; color: string; state: 'missing' | 'continuing' | 'complete' } {
  const aired = s.aired ?? 0;
  const have = Math.min(s.have ?? 0, aired);
  const fill = aired > 0 ? have / aired : 0;
  if (have < aired) return { fill, color: '#ef4444', state: 'missing' };
  if (s.continuing) return { fill, color: ACCENT, state: 'continuing' };
  return { fill, color: '#22c55e', state: 'complete' };
}
export const episodesText = (s: Show) => `${Math.min(s.have ?? 0, s.aired ?? 0)} of ${s.aired ?? 0} episodes`;

export type SeriesLayout = 'posters' | 'list';
const LAYOUT_KEY = 'luma.tv.series.layout';
const savedLayout = (): SeriesLayout => { try { return localStorage.getItem(LAYOUT_KEY) === 'list' ? 'list' : 'posters'; } catch { return 'posters'; } };

export function TvSeries({ shows, onChanged, onOpen, add }: {
  shows: Show[] | null; onChanged?: () => void;
  /** v182: the Add series button (its own flow), at the end of the bar row */
  add?: React.ReactNode;
  /** v182: open the show's detail page (else a poster opens Edit and a list title the show sheet) */
  onOpen?: (s: Show) => void;
}) {
  const sorted = useMemo(() => (shows ? sortShows(shows) : []), [shows]);
  const [layout, setLayoutState] = useState<SeriesLayout>(savedLayout);
  const setLayout = (l: SeriesLayout) => { setLayoutState(l); setPicked(new Set()); setSelecting(false); setBulk(''); try { localStorage.setItem(LAYOUT_KEY, l); } catch { /* private mode */ } };
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [sheet, setSheet] = useState<Show | null>(null);
  useEffect(() => {
    if (layout !== 'list') return;
    let live = true;
    void loadProfiles().then(r => { if (live && r.ok) setProfiles(r.data.profiles); });
    return () => { live = false; };
  }, [layout]);
  const [editing, setEditing] = useState<Show | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [bulk, setBulk] = useState<'' | 'busy' | 'error'>('');
  const toggle = (id: number) => setPicked(p => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const stopSelecting = () => { setSelecting(false); setPicked(new Set()); setBulk(''); };
  const runMissing = async () => {
    if (!picked.size) return;
    setBulk('busy');
    const r = await monitorMissing([...picked]);
    if (!r.ok) { setBulk('error'); return; }
    stopSelecting();
    onChanged?.();
  };
  const firsts = useMemo(() => {
    const m = new Map<string, number>();
    sorted.forEach(s => { const l = letterOf(s.title); if (!m.has(l)) m.set(l, s.id); });
    return m;
  }, [sorted]);
  const [current, setCurrent] = useState<string | null>(null);
  const pinned = useRef<{ mode: string; left: number; height?: number }>({ mode: '', left: -1 });
  const col = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const railRef = useRef<HTMLElement>(null);
  const jumped = useRef<string | null>(null);         // a rail click wins while its show is on screen (the page end)

  // a name that needs a 3rd line at 13: 12/16, then more lines at 12/16, never smaller (fitCaptions)
  useLayoutEffect(() => {
    if (!sorted.length) return;
    let frame = 0;
    const fit = () => fitCaptions(root.current);
    fit();
    const onResize = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(fit); };
    window.addEventListener('resize', onResize);
    return () => { cancelAnimationFrame(frame); window.removeEventListener('resize', onResize); };
  }, [sorted, layout]);

  useEffect(() => {
    if (!sorted.length) return;
    const measure = () => {
      const tiles = root.current?.querySelectorAll<HTMLElement>('[data-letter]') || [];
      let cur: string | null = null;
      for (const t of tiles) { if (t.getBoundingClientRect().bottom > 80) { cur = t.dataset.letter || null; break; } }
      const j = jumped.current && root.current?.querySelector<HTMLElement>(`[data-letter="${jumped.current}"]`);
      if (j && j.getBoundingClientRect().top >= -1) cur = jumped.current;      // still on its way, or the page ended
      else jumped.current = null;
      setCurrent(cur);
    };
    // sticky at top 24px, by hand: the app's body is a scroll container (overflow: auto), which pins
    // position: sticky to it instead of the page. While stuck the rail is position: fixed (the compositor
    // keeps it still: no per-frame moves). The pin is set synchronously, in the scroll event that crosses
    // the edge (two rect reads, then style writes only when the mode changes), so the frame that crosses
    // is already painted pinned: no one-frame snap.
    const pin = () => {
      const nav = railRef.current;
      const box = root.current?.getBoundingClientRect();
      if (!nav || !box) return;
      const h = railHeight(RAIL_TOP);
      const left = Math.round(col.current?.getBoundingClientRect().left || 0);
      const mode = box.top >= RAIL_TOP || box.height <= h ? 'top' : box.bottom - h < RAIL_TOP ? 'bottom' : 'fixed';
      const height = mode === 'top' ? railHeight(box.top) : h;
      const p = pinned.current;
      if (p.mode === mode && p.height === height && (mode !== 'fixed' || p.left === left)) return;
      pinned.current = { mode, left, height };
      nav.dataset.pin = mode;
      nav.style.height = `${height}px`;
      Object.assign(nav.style, mode === 'fixed'
        ? { position: 'fixed', top: `${RAIL_TOP}px`, bottom: '', left: `${left}px` }
        : mode === 'bottom' ? { position: 'absolute', top: '', bottom: '0px', left: '0px' }
          : { position: 'absolute', top: '0px', bottom: '', left: '0px' });
    };
    // the current letter: at most once a frame (a few rects in the rAF, one React state update)
    let frame = 0;
    const onEvent = () => { pin(); if (!frame) frame = window.requestAnimationFrame(() => { frame = 0; measure(); }); };
    pinned.current = { mode: '', left: -1 };
    pin();
    measure();
    window.addEventListener('scroll', onEvent, { passive: true, capture: true });
    window.addEventListener('resize', onEvent);
    const user = () => { jumped.current = null; };                    // the user scrolls on their own: follow the page
    window.addEventListener('wheel', user, { passive: true });
    window.addEventListener('keydown', user);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener('scroll', onEvent, { capture: true }); window.removeEventListener('resize', onEvent);
      window.removeEventListener('wheel', user); window.removeEventListener('keydown', user);
    };
  }, [sorted, layout]);

  if (shows === null) {
    return (
      <div data-testid="tv-series-loading" aria-busy="true" style={{ ...grid, paddingRight: RAIL.width }}>
        {Array.from({ length: 12 }, (_, i) => (
          <span key={i} style={{ display: 'block', aspectRatio: '2 / 3', borderRadius: 8, background: 'rgba(255,255,255,0.04)' }} />
        ))}
      </div>
    );
  }
  if (!sorted.length) {
    const empty = <div data-testid="tv-series-empty" style={emptyCard}>No shows</div>;
    return add ? (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, fontFamily: FONT }}>
        <div data-testid="tv-series-bar-row" style={{ display: 'flex', justifyContent: 'flex-end', minHeight: 36 }}>{add}</div>
        {empty}
      </div>
    ) : empty;
  }
  const jump = (l: string) => {
    const id = firsts.get(l);
    if (id === undefined) return;
    jumped.current = l;
    root.current?.querySelector(`[data-show-id="${id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setCurrent(l);
  };
  const toggleBar = (
    <div role="radiogroup" aria-label="Layout" data-testid="tv-series-layout" style={{ display: 'inline-flex', gap: 2, padding: 2, borderRadius: 10,
      background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', marginRight: 'auto' }}>
      {(['posters', 'list'] as const).map(l => (
        <button key={l} type="button" role="radio" aria-checked={layout === l} data-testid={`tv-series-layout-${l}`} className="lp-focus tvs-glass"
          onClick={() => setLayout(l)}
          style={{ height: 30, padding: '0 12px', borderRadius: 8, border: 'none', fontFamily: FONT, fontSize: 13, fontWeight: 600, cursor: 'pointer',
            color: layout === l ? '#fff' : W50, background: layout === l ? 'rgba(255,255,255,0.12)' : 'transparent' }}>
          {l === 'posters' ? 'Posters' : 'List'}
        </button>
      ))}
    </div>
  );
  const sheetEl = sheet && (
    <ShowSheet seriesId={sheet.id} title={sheet.title} onClose={() => setSheet(null)} onChanged={() => onChanged?.()} />
  );
  if (layout === 'list') {
    const ids = sorted.filter(s => picked.has(s.id)).map(s => s.id);
    return (
      <div data-testid="tv-series-wrap" data-layout="list" style={{ display: 'flex', flexDirection: 'column', gap: 12, fontFamily: FONT }}>
        <style>{tvSeriesCss}</style>
        <div data-testid="tv-series-bar-row" style={{ display: 'flex', alignItems: 'center', gap: 8, minHeight: 36 }}>
          {toggleBar}
          <span data-testid="tv-series-picked" style={{ fontSize: 13, fontWeight: 600, color: W50 }}>{`${ids.length} selected`}</span>
          {add}
        </div>
        <SeriesList shows={sorted} profiles={profiles} picked={picked} setPicked={setPicked} onOpen={onOpen || setSheet} onMonitored={() => onChanged?.()} />
        {ids.length > 0 && (
          <BulkBar ids={ids} profiles={profiles} onClear={() => setPicked(new Set())}
            onDone={() => { setPicked(new Set()); onChanged?.(); }} />
        )}
        {sheetEl}
      </div>
    );
  }
  return (
    <div data-testid="tv-series-wrap" style={{ display: 'flex', flexDirection: 'column', gap: 12, fontFamily: FONT }}>
    <div data-testid="tv-series-bar-row" style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8, minHeight: 36, paddingRight: RAIL.width }}>
      {!selecting && toggleBar}
      {selecting ? (
        <>
          <span data-testid="tv-series-picked" style={{ fontSize: 13, fontWeight: 600, color: W50, marginRight: 'auto' }}>{`${picked.size} selected`}</span>
          {bulk === 'error' && <span role="alert" style={{ fontSize: 13, color: '#ef4444' }}>{"Couldn't save"}</span>}
          <button type="button" className="lp-focus tvs-glass" data-testid="tv-series-cancel" onClick={stopSelecting} style={barBtn(false)}>Cancel</button>
          <button type="button" className="lp-focus" data-testid="tv-series-missing" disabled={!picked.size || bulk === 'busy'} onClick={() => void runMissing()}
            style={{ ...barBtn(true), opacity: picked.size && bulk !== 'busy' ? 1 : 0.4, cursor: picked.size ? 'pointer' : 'default' }}>Monitor missing</button>
        </>
      ) : (
        <button type="button" className="lp-focus tvs-glass" data-testid="tv-series-select" onClick={() => setSelecting(true)} style={barBtn(false)}>Select</button>
      )}
      {!selecting && add}
    </div>
    <div ref={root} data-testid="tv-series" style={{ display: 'flex', alignItems: 'flex-start', fontFamily: FONT }}>
      <div style={{ ...grid, flex: 1, minWidth: 0 }}>
        {sorted.map(s => (
          <Tile key={s.id} s={s} first={firsts.get(letterOf(s.title)) === s.id} selecting={selecting} picked={picked.has(s.id)}
            opens={!!onOpen} onOpen={() => (selecting ? toggle(s.id) : onOpen ? onOpen(s) : setEditing(s))} />
        ))}
      </div>
      <style>{tvSeriesCss}</style>
      <div ref={col} style={{ width: RAIL.width, flex: 'none', alignSelf: 'stretch', position: 'relative' }}>
      {/* position, top/bottom/left and data-pin are set by pin() (not by React, so a re-render never undoes them) */}
      <nav ref={railRef} aria-label="Jump to letter" data-testid="tv-series-rail"
        style={{ width: RAIL.width, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', justifyContent: 'space-between',
          boxSizing: 'border-box' }}>
        {LETTERS.map(l => {
          const has = firsts.has(l);
          const on = has && l === current;
          return (
            <button key={l} type="button" data-testid={`tv-rail-${l}`} aria-disabled={!has} tabIndex={has ? 0 : -1}
              onClick={() => has && jump(l)} className={has ? 'lp-focus tvs-rail-btn' : 'tvs-rail-btn'}
              style={{ width: RAIL.target, height: RAIL.target, flex: 'none', padding: 0, border: 'none', background: 'none', fontFamily: FONT,
                fontSize: RAIL.font, fontWeight: 600, lineHeight: `${RAIL.target}px`, textAlign: 'center', cursor: has ? 'pointer' : 'default', pointerEvents: has ? 'auto' : 'none',
                color: on ? ACCENT : has ? W50 : 'rgba(255,255,255,0.2)' }}>
              {l}
            </button>
          );
        })}
      </nav>
      </div>
    </div>
    {editing && (
      <EditSeriesModal seriesId={editing.id} title={editing.title} onClose={() => setEditing(null)}
        onSaved={() => onChanged?.()} onDeleted={() => onChanged?.()} />
    )}
    </div>
  );
}

const barBtn = (primary: boolean): React.CSSProperties => ({
  height: 36, padding: '0 14px', borderRadius: 10, fontSize: 13, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', color: '#fff',
  border: primary ? 'none' : '1px solid rgba(255,255,255,0.08)', background: primary ? ACCENT : 'rgba(255,255,255,0.06)',
});

function Tile({ s, first, selecting, picked, onOpen, opens = false }: {
  s: Show; first: boolean; selecting: boolean; picked: boolean; onOpen: () => void; opens?: boolean;
}) {
  const p = progress(s);
  const src = posterUrl(s.poster_path, 'w342');
  const [broken, setBroken] = useState(false);
  return (
    <div data-testid="tv-series-tile" data-show-id={s.id} title={s.title} data-letter={first ? letterOf(s.title) : undefined}
      data-state={p.state} data-monitored={s.monitored ? 'true' : 'false'} style={{ minWidth: 0, scrollMarginTop: 24, display: 'flex', flexDirection: 'column' }}>
      <button type="button" className="lp-focus tvs-poster" data-testid="tv-series-open" title={s.title}
        aria-label={selecting || opens ? s.title : `Edit ${s.title}`} aria-pressed={selecting ? picked : undefined} onClick={onOpen}
        style={{ display: 'block', width: '100%', padding: 0, border: 'none', background: 'none', cursor: 'pointer', textAlign: 'left' }}>
      <div style={{ position: 'relative', aspectRatio: '2 / 3', borderRadius: '8px 8px 0 0', overflow: 'hidden', background: 'rgba(255,255,255,0.06)',
        outline: picked ? `2px solid ${ACCENT}` : undefined, outlineOffset: -2 }}>
        {selecting && (
          <span data-testid="tv-series-check" aria-hidden="true" style={{ position: 'absolute', top: 8, left: 8, zIndex: 1, width: 20, height: 20, borderRadius: 6,
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: picked ? ACCENT : 'rgba(21,16,42,.6)',
            border: picked ? 'none' : '1.5px solid rgba(255,255,255,0.6)' }}>
            {picked && <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5" /></svg>}
          </span>
        )}
        {src && !broken ? (
          <img src={src} alt={s.title} loading="lazy" onError={() => setBroken(true)}
            style={{ display: 'block', width: '100%', height: '100%', objectFit: 'cover' }} />
        ) : (
          <span data-testid="tv-series-tile-tmdb" aria-hidden="true"
            style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <img src={RATING_ICONS.tmdb} alt="" style={{ width: '40%', height: 'auto', opacity: 0.8 }} />
          </span>
        )}
      </div>
      </button>
      <div data-testid="tv-series-bar" role="progressbar" aria-label={`${s.title}: ${episodesText(s)}`}
        aria-valuemin={0} aria-valuemax={s.aired ?? 0} aria-valuenow={Math.min(s.have ?? 0, s.aired ?? 0)}
        style={{ height: 4, background: 'rgba(255,255,255,0.08)' }}>
        <span style={{ display: 'block', height: 4, width: `${Math.round(p.fill * 1000) / 10}%`, background: p.color }} />
      </div>
      <div data-testid="tv-series-caption" style={{ paddingTop: 8, display: 'flex', flexDirection: 'column', justifyContent: 'flex-start', gap: 2,
        minHeight: CAPTION_MIN_H, flex: 1, boxSizing: 'border-box' }}>
        <span data-testid="tv-series-name" title={s.title} aria-label={s.title} style={caption}>{s.title}</span>
        <span data-testid="tv-series-count" style={{ fontSize: 12, color: W50, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{episodesText(s)}</span>
      </div>
    </div>
  );
}

/** The caption block (name + count) is at least this tall, so posters and bars line up row to row and any
 * space falls below the count (8 top + 2 x 17 + 2 + 15 = 59 with padding; 51 without it). */
const CAPTION_MIN_H = 51 + 8;
/** Caption type: 13/600, normal tracking, 17px lines; the count sits right under the name.
 * Steps: a name that would need a 3rd line at 13 is set 12/16 on 2 lines; still over, it is set 12/16 on
 * as many lines as it needs (v168: PM defers to Designer: no line cap, no ellipsis, never below 12px). The
 * full name is also the card's title and accessible name. The grid row stretches every tile to the row's
 * height and the caption block fills it, so all captions in a row share one height. */
export const CAPTION_TYPE = { size: 13, line: 17 };
export const CAPTION_FIT = { size: 12, line: 16 };
/** The floor is the fit size: captions never go below 12px. */
export const CAPTION_FLOOR = CAPTION_FIT;
const caption: React.CSSProperties = {
  fontSize: CAPTION_TYPE.size, letterSpacing: 'normal', fontWeight: 600, lineHeight: `${CAPTION_TYPE.line}px`, color: W95, overflow: 'hidden', overflowWrap: 'anywhere',
  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
};
const over = (n: HTMLElement) => n.scrollHeight > n.clientHeight + 1;
const setType = (n: HTMLElement, t: { size: number; line: number }, lines: number) => {
  n.style.display = '-webkit-box'; n.style.overflow = 'hidden';
  n.style.fontSize = `${t.size}px`; n.style.lineHeight = `${t.line}px`;
  n.style.setProperty('-webkit-line-clamp', String(lines)); n.style.maxHeight = `${t.line * lines}px`;
};
/** No clamp at all: 12/16 on every line the name needs (nothing hidden, so nothing to ellipsize). */
const setFull = (n: HTMLElement) => {
  n.style.display = 'block'; n.style.overflow = 'visible';
  n.style.fontSize = `${CAPTION_FIT.size}px`; n.style.lineHeight = `${CAPTION_FIT.line}px`;
  n.style.removeProperty('-webkit-line-clamp'); n.style.maxHeight = 'none';
};
/** Batched (reset and read all, then write the next step to those still over); returns how many are
 * still cut (0: every name is shown in full). */
export function fitCaptions(root: HTMLElement | null): number {
  const names = [...(root?.querySelectorAll<HTMLElement>('[data-testid=tv-series-name]') || [])];
  for (const n of names) { setType(n, CAPTION_TYPE, 2); delete n.dataset.fit; delete n.dataset.lines; }
  let left = names.filter(over);
  for (const n of left) { setType(n, CAPTION_FIT, 2); n.dataset.fit = String(CAPTION_FIT.size); n.dataset.lines = '2'; }
  left = left.filter(over);
  for (const n of left) setFull(n);
  for (const n of left) n.dataset.lines = String(Math.max(3, Math.round(n.scrollHeight / CAPTION_FIT.line)));
  return names.filter(n => n.style.display !== 'block' && over(n)).length;
}
const grid: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(128px, 1fr))', gap: 16 };
const emptyCard: React.CSSProperties = {
  borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', height: 160,
  display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 500, color: 'rgba(255,255,255,0.7)', fontFamily: FONT,
};
