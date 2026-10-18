/** Settings > TV > Series (desktop web): every library show as a 2:3 poster,
 * A–Z ignoring punctuation and a leading The/A/An, with a downloaded-share bar and "N of M
 * episodes", and a sticky # – Z rail. Posters aren't clickable (no show detail yet). */
import { useEffect, useMemo, useRef, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { posterUrl } from '@/utils/image';
import { RATING_ICONS } from '@/utils/ratingIcons';
import type { Show } from './tvLibraryApi';

const W50 = 'rgba(255,255,255,0.5)';
const W95 = 'rgba(255,255,255,0.95)';
const ACCENT = '#3b82f6';
const RAIL_TOP = 24;
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

export function TvSeries({ shows }: { shows: Show[] | null }) {
  const sorted = useMemo(() => (shows ? sortShows(shows) : []), [shows]);
  const firsts = useMemo(() => {
    const m = new Map<string, number>();
    sorted.forEach(s => { const l = letterOf(s.title); if (!m.has(l)) m.set(l, s.id); });
    return m;
  }, [sorted]);
  const [current, setCurrent] = useState<string | null>(null);
  const pinned = useRef<{ mode: string; left: number }>({ mode: '', left: -1 });
  const col = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const railRef = useRef<HTMLElement>(null);
  const jumped = useRef<string | null>(null);         // a rail click wins while its show is on screen (the page end)

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
      const h = nav.offsetHeight;
      const left = Math.round(col.current?.getBoundingClientRect().left || 0);
      const mode = box.top >= RAIL_TOP || box.height <= h ? 'top' : box.bottom - h < RAIL_TOP ? 'bottom' : 'fixed';
      const p = pinned.current;
      if (p.mode === mode && (mode !== 'fixed' || p.left === left)) return;
      pinned.current = { mode, left };
      nav.dataset.pin = mode;
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
  }, [sorted]);

  if (shows === null) {
    return (
      <div data-testid="tv-series-loading" aria-busy="true" style={{ ...grid, paddingRight: 28 }}>
        {Array.from({ length: 12 }, (_, i) => (
          <span key={i} style={{ display: 'block', aspectRatio: '2 / 3', borderRadius: 8, background: 'rgba(255,255,255,0.04)' }} />
        ))}
      </div>
    );
  }
  if (!sorted.length) {
    return <div data-testid="tv-series-empty" style={emptyCard}>No shows</div>;
  }
  const jump = (l: string) => {
    const id = firsts.get(l);
    if (id === undefined) return;
    jumped.current = l;
    root.current?.querySelector(`[data-show-id="${id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setCurrent(l);
  };
  return (
    <div ref={root} data-testid="tv-series" style={{ display: 'flex', alignItems: 'flex-start', fontFamily: FONT }}>
      <div style={{ ...grid, flex: 1, minWidth: 0 }}>
        {sorted.map(s => <Tile key={s.id} s={s} first={firsts.get(letterOf(s.title)) === s.id} />)}
      </div>
      <div ref={col} style={{ width: 28, flex: 'none', alignSelf: 'stretch', position: 'relative' }}>
      {/* position, top/bottom/left and data-pin are set by pin() (not by React, so a re-render never undoes them) */}
      <nav ref={railRef} aria-label="Jump to letter" data-testid="tv-series-rail"
        style={{ width: 28, display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
        {LETTERS.map(l => {
          const has = firsts.has(l);
          const on = has && l === current;
          return (
            <button key={l} type="button" data-testid={`tv-rail-${l}`} aria-disabled={!has} tabIndex={has ? 0 : -1}
              onClick={() => has && jump(l)} className={has ? 'lp-focus' : undefined}
              style={{ width: 20, height: 20, padding: 0, border: 'none', background: 'none', fontFamily: FONT, fontSize: 11, fontWeight: 600,
                lineHeight: '20px', textAlign: 'center', cursor: has ? 'pointer' : 'default', pointerEvents: has ? 'auto' : 'none',
                color: on ? ACCENT : has ? W50 : 'rgba(255,255,255,0.2)' }}>
              {l}
            </button>
          );
        })}
      </nav>
      </div>
    </div>
  );
}

function Tile({ s, first }: { s: Show; first: boolean }) {
  const p = progress(s);
  const src = posterUrl(s.poster_path, 'w342');
  const [broken, setBroken] = useState(false);
  return (
    <div data-testid="tv-series-tile" data-show-id={s.id} data-letter={first ? letterOf(s.title) : undefined}
      data-state={p.state} style={{ minWidth: 0, scrollMarginTop: 24 }}>
      <div style={{ position: 'relative', aspectRatio: '2 / 3', borderRadius: '8px 8px 0 0', overflow: 'hidden', background: 'rgba(255,255,255,0.06)' }}>
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
      <div data-testid="tv-series-bar" role="progressbar" aria-label={`${s.title}: ${episodesText(s)}`}
        aria-valuemin={0} aria-valuemax={s.aired ?? 0} aria-valuenow={Math.min(s.have ?? 0, s.aired ?? 0)}
        style={{ height: 4, background: 'rgba(255,255,255,0.08)' }}>
        <span style={{ display: 'block', height: 4, width: `${Math.round(p.fill * 1000) / 10}%`, background: p.color }} />
      </div>
      <div style={{ paddingTop: 8, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span data-testid="tv-series-name" title={s.title} aria-label={s.title} style={caption}>{s.title}</span>
        <span data-testid="tv-series-count" style={{ fontSize: 12, color: W50, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{episodesText(s)}</span>
      </div>
    </div>
  );
}

/** 13/600, 17px lines, at most 2 then an ellipsis; always 2 lines tall so posters and bars line up row to row. */
const caption: React.CSSProperties = {
  fontSize: 13, fontWeight: 600, lineHeight: '17px', minHeight: 34, color: W95, overflow: 'hidden', overflowWrap: 'anywhere',
  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
};
const grid: React.CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(128px, 1fr))', gap: 16 };
const emptyCard: React.CSSProperties = {
  borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', height: 160,
  display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 500, color: 'rgba(255,255,255,0.7)', fontFamily: FONT,
};
