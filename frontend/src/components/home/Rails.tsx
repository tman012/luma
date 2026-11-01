/** Phone-first rails on Luma's purple-navy glass. */
import { CARD_SIZES, cardSrcSet, img, type Card, type Rail } from './homeApi';
import { FONT } from '@/components/start/StartUI';
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { MOTION, reducedMotion } from '@/theme/motion';
import { PRESS_RELEASED_EVENT, scheduleWarm, tapPending, warmDetail } from '@/services/modalPrefetch';
import { OwnedOrb, plexOwned, useRailOwnership, type CardOwnership } from './railOwnership';
import { LONG_PRESS_STYLE, useLongPress } from './useLongPress';

export const GLASS: React.CSSProperties = {
  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
};

export function RailRow({ title, children, testId, prod }: { title: string; children: React.ReactNode; testId?: string; prod?: boolean }) {
  return (
    <section style={prod ? wlRailWrap : railWrap} data-testid={testId}>
      <h2 style={prod ? wlRailTitle : railTitle}>{title}</h2>
      <div style={prod ? wlScroller : scroller} className="luma-rail">{children}</div>
    </section>
  );
}

/** The ids openDetail will open a card's popup with. */
export function cardIds(c: Card): { kind: 'movie' | 'tv'; ratingKey?: number; tmdbId?: number } {
  const kind = c.type === 'movie' ? 'movie' : 'tv';
  if (c.tmdb_id) return { kind, tmdbId: c.tmdb_id };
  return { kind, ratingKey: Number(kind === 'movie' ? c.key : (c.show_key || c.key)) };
}

/** Warm the detail popup (metadata, backdrop, logo, color blur). */
export function warmCard(c: Card): Promise<void> {
  const { kind, ratingKey, tmdbId } = cardIds(c);
  return warmDetail(kind, ratingKey, tmdbId).catch(() => {});
}

/** Home's background warm-ups share one cap where the link can't be checked (UNKNOWN_LINK_CAP). */
export const HOME_WARM_GROUP = 'home';

/** One warm-up per title (the same title in two rails shares it). */
export function warmKey(c: Card): string {
  const { kind, ratingKey, tmdbId } = cardIds(c);
  return `${kind}:${ratingKey ?? ''}:${tmdbId ?? ''}`;
}

type OnOpen = ((card: Card, el?: HTMLElement | null) => void) | undefined;

/** Cards on or near the screen get warmed (4 at a time); one that scrolls
 *  away before its turn is dropped. */
function useWarmWhenVisible(ref: React.RefObject<HTMLElement | null>, card: Card, on: boolean): void {
  useEffect(() => {
    const el = ref.current;
    if (!on || !el || typeof IntersectionObserver === 'undefined') return;
    let cancel = () => {};
    const io = new IntersectionObserver((entries) => {
      const e = entries[entries.length - 1];
      cancel();
      cancel = e?.isIntersecting ? scheduleWarm(warmKey(card), () => warmCard(card), HOME_WARM_GROUP) : () => {};
    }, { rootMargin: '150px' });
    io.observe(el);
    return () => { io.disconnect(); cancel(); };
  }, [ref, card, on]);
}

type OnLongPress = ((card: Card) => void) | undefined;

/** A card is a plain button when it can open the detail popup. v254: with *onLongPress* (What you're missing),
 * a long press opens the dismiss sheet instead (prod's MobilePosterCard gesture, useLongPress). */
function CardShell({ card, onOpen, onLongPress, style, children, own }: { card: Card; onOpen: OnOpen; onLongPress?: OnLongPress; style: React.CSSProperties; children: React.ReactNode; own?: CardOwnership }) {
  const ref = useRef<HTMLButtonElement>(null);
  useWarmWhenVisible(ref, card, !!onOpen);
  const lp = useLongPress(onLongPress ? () => onLongPress(card) : undefined);
  if (!onOpen && !lp) return <div style={style}>{children}</div>;
  const warm = () => { void warmCard(card); };
  return (
    <button ref={ref} type="button" aria-label={card.title}
      onClick={(e) => { if (lp?.consumeClick()) return; onOpen?.(card, e.currentTarget); }}
      onPointerEnter={warm} onPointerDown={warm} onFocus={warm}
      onTouchStart={(e) => { warm(); lp?.onTouchStart(e); }}
      onTouchMove={lp?.onTouchMove} onTouchEnd={lp?.onTouchEnd} onTouchCancel={lp?.onTouchCancel}
      onContextMenu={lp?.onContextMenu}
      data-testid={`card-${card.key}`} data-owned={own ? String(own.owned) : undefined} className={lp ? 'luma-pressable longpress-poster' : 'luma-pressable'}
      style={{ ...cardButton, ...style, ...(lp ? LONG_PRESS_STYLE : null) }}>
      {children}
    </button>
  );
}

export function PosterCard({ card, onOpen, own, onLongPress }: { card: Card; onOpen?: OnOpen; own?: CardOwnership; onLongPress?: OnLongPress }) {
  const src = img(card.poster);
  return (
    <CardShell card={card} onOpen={onOpen} onLongPress={onLongPress} style={posterCard} own={own ?? plexOwned(card) ?? undefined}>
      <div style={{ ...posterImg, ...GLASS }}>
        {src && <img src={src} srcSet={cardSrcSet(src, 'poster')} sizes={CARD_SIZES.poster} alt="" loading="lazy" style={fill} draggable={false} />}
        <OwnedOrb own={own} />
      </div>
      {card.label && <div style={cardLabel} data-testid="card-label">{card.label}</div>}
      <div style={card.label ? { ...cardTitle, marginTop: 2 } : cardTitle}>{card.title}</div>
      {card.subtitle && <div style={cardSub}>{card.subtitle}</div>}
    </CardShell>
  );
}

/** v213 (Designer): the Watchlist rail card is prod 1b4ea74's AuthenticatedHomePage poster card as is: 116x174,
 * radius 10, no border; label 10/600 above a 12/500 title and a 10px year, prod's font stack. */
export function WatchlistPosterCard({ card, onOpen }: { card: Card; onOpen?: OnOpen }) {
  const src = img(card.poster);
  return (
    <CardShell card={card} onOpen={onOpen} style={wlCard}>
      <div style={wlPoster}>
        {src && <img src={src} srcSet={cardSrcSet(src, 'poster')} sizes={CARD_SIZES.watchlist} alt="" loading="lazy" style={fill} draggable={false} />}
      </div>
      <div style={wlText}>
        {card.label && <p style={wlLabel} data-testid="card-label">{card.label}</p>}
        <p style={wlTitle}>{card.title}</p>
        {card.subtitle && <p style={wlSub}>{card.subtitle}</p>}
      </div>
    </CardShell>
  );
}

export function WideCard({ card, onOpen, own }: { card: Card; onOpen?: OnOpen; own?: CardOwnership }) {
  const src = img(card.art || card.poster);
  const pct = Math.max(0, Math.min(1, card.progress || 0));
  return (
    <CardShell card={card} onOpen={onOpen} style={wideCard}>
      <div style={{ ...wideImg, ...GLASS }}>
        {src && <img src={src} srcSet={cardSrcSet(src, 'wide')} sizes={CARD_SIZES.wide} alt="" loading="lazy" style={fill} draggable={false} />}
        {pct > 0 && (
          <div style={barTrack}><div data-testid="progress" style={{ ...barFill, width: `${pct * 100}%` }} /></div>
        )}
        <OwnedOrb own={own} />
      </div>
      <div style={cardTitle}>{card.title}</div>
      {card.subtitle && <div style={cardSub}>{card.subtitle}</div>}
    </CardShell>
  );
}

/** Home skeleton layout (Designer 1:03 AM ET): the skeleton is drawn from the last Home that rendered, so its
 *  rails (and the pills under the first one) sit where the content will. Generic: per rendered rail only its shape
 *  (wide = Continue Watching cards, else posters), box height and top margin; no titles, ids or user data. */
export type SkeletonRail = { wide: boolean; h: number; mt: number };
export const HOME_LAYOUT_KEY = 'luma.homeSkeletonLayout.v1';
/** First-ever load (no history): poster rails, the first one 265px (the common no-CW Home). */
export const DEFAULT_SKELETON_LAYOUT: SkeletonRail[] = [{ wide: false, h: 265, mt: 28 }, { wide: false, h: 265, mt: 28 }, { wide: false, h: 265, mt: 28 }];
export function readHomeLayout(): SkeletonRail[] {
  try {
    const v = JSON.parse(localStorage.getItem(HOME_LAYOUT_KEY) || 'null');
    if (Array.isArray(v) && v.length && v.every(r => r && typeof r.h === 'number' && r.h > 0 && r.h < 2000 && typeof r.mt === 'number'))
      return v.slice(0, 3).map(r => ({ wide: !!r.wide, h: Math.round(r.h), mt: Math.round(r.mt) }));
  } catch { /* private mode / bad JSON: the default */ }
  return DEFAULT_SKELETON_LAYOUT;
}
function saveHomeLayout(root: HTMLElement | null) {
  if (!root) return;
  const rails = Array.from(root.children).filter((e): e is HTMLElement => e.tagName === 'SECTION').slice(0, 3)
    .map(e => ({ wide: e.getAttribute('data-testid') === 'rail-continue', h: e.offsetHeight, mt: parseFloat(getComputedStyle(e).marginTop) || 0 }));
  if (!rails.length || rails.some(r => !r.h)) return;
  try { localStorage.setItem(HOME_LAYOUT_KEY, JSON.stringify(rails)); } catch { /* quota / private mode */ }
}

/** v289: afterFirst sits under the first skeleton rail (the same slot as loaded HomeRails: after Continue Watching,
 *  else after the first rail), so the pills don't paint at the top and then jump. Each rail's box is the last Home's. */
export function SkeletonRails({ afterFirst, afterH, ghost, layout }: { rails?: number; afterFirst?: React.ReactNode; afterH?: number; ghost?: boolean; layout?: SkeletonRail[] }) {
  const rails = layout ?? readHomeLayout();
  return (
    <div data-testid={ghost ? undefined : 'home-skeleton'} aria-busy={ghost ? undefined : 'true'}>
      {rails.map((rl, r) => (
        <Fragment key={r}>
          <section style={{ marginTop: rl.mt, height: rl.h, overflow: 'hidden' }} data-skel-rail={rl.wide ? 'wide' : 'poster'}>
            <div className={ghost ? undefined : 'luma-shimmer'} style={{ ...skel, ...(ghost ? GHOST_FILL : null), width: 140, height: 18, borderRadius: 6, marginLeft: 20 }} />
            <div style={scroller}>
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} style={rl.wide ? wideCard : posterCard}>
                  <div className={ghost ? undefined : 'luma-shimmer'} style={{ ...skel, ...(ghost ? GHOST_FILL : null), ...(rl.wide ? wideImg : posterImg) }} />
                </div>
              ))}
            </div>
          </section>
          {r === 0 && (afterH != null ? <div style={{ height: afterH }} /> : afterFirst)}
        </Fragment>
      ))}
    </div>
  );
}

/** Home only (MOTION.md exception 11:32 PM ET): the skeleton crossfades to the content in the same box over
 *  MOTION.dur.crossfade. The outgoing skeleton stays on top, absolutely placed (no layout), and fades out while the
 *  content fades in under it; the pills slot keeps its measured height so the lower skeleton rails don't move. */
export function SkeletonCrossfade({ loading, children }: { loading: boolean; children: React.ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const afterH = useRef<number | undefined>(undefined);
  const layoutRef = useRef<SkeletonRail[] | undefined>(undefined);
  const was = useRef(loading);
  const [ghost, setGhost] = useState(false);
  const [hold, setHold] = useState(false);
  useLayoutEffect(() => {
    if (loading) {
      layoutRef.current = readHomeLayout();
      const a = box.current?.querySelector<HTMLElement>('[data-testid="home-skeleton"] > section + :not(section)');
      afterH.current = a ? a.offsetHeight : 0;
    }
    const left = was.current && !loading;
    was.current = loading;
    if (!left || reducedMotion()) return;
    setGhost(true);
    // A tap is pending (card pressed, sheet not moving yet): hold both fades at their first frame (ghost opaque,
    // content transparent under it) until the sheet moves, so the crossfade never competes with the sheet start.
    let t = 0;
    const run = () => { setHold(false); window.clearTimeout(t); t = window.setTimeout(() => setGhost(false), MOTION.dur.crossfade); };
    if (tapPending()) { setHold(true); window.addEventListener(PRESS_RELEASED_EVENT, run, { once: true }); }
    else t = window.setTimeout(() => setGhost(false), MOTION.dur.crossfade);
    return () => { window.clearTimeout(t); window.removeEventListener(PRESS_RELEASED_EVENT, run); };
  }, [loading]);
  return (
    <div ref={box} data-xf-hold={hold ? '' : undefined} style={{ position: 'relative' }}>
      {children}
      {ghost && !loading && (
        <div aria-hidden="true" data-testid="home-skeleton-out" className="luma-skel-out"
          style={{ position: 'absolute', top: 0, left: 0, right: 0, pointerEvents: 'none' }}>
          <SkeletonRails ghost afterH={afterH.current ?? 0} layout={layoutRef.current} />
        </div>
      )}
    </div>
  );
}

/** v254: What you're missing (app/daily_missing), a regular Home rail; its cards long-press to dismiss. */
export const WYM_RAIL_ID = 'what_youre_missing';

export function HomeRails({ cw, rails, onOpen, onDismiss, afterFirst }: { cw: Card[]; rails: Rail[]; onOpen?: (card: Card, el?: HTMLElement | null) => void; onDismiss?: (card: Card) => void; afterFirst?: React.ReactNode }) {
  // v181: prod's rule: no orb on all-owned library rails (Continue watching, every Plex library rail);
  // the orb only on mixed rails (Trakt), resolved by prod's batch check.
  const own = useRailOwnership(rails.flatMap(r => r.items).filter(c => c.tmdb_id));
  const root = useRef<HTMLDivElement>(null);
  // The next cold load's skeleton is drawn from this layout (after paint: never on the tap path).
  useEffect(() => { const t = window.setTimeout(() => saveHomeLayout(root.current), 500); return () => window.clearTimeout(t); });
  return (
    <div ref={root} data-testid="home-rails" className="luma-rails-in">
      {cw.length > 0 && (
        <RailRow title="Continue Watching" testId="rail-continue">
          {cw.map(c => <WideCard key={c.key} card={c} onOpen={onOpen} />)}
        </RailRow>
      )}
      {/* v260: prod's Home row goes after Continue watching, else after the first rail. */}
      {cw.length > 0 && afterFirst}
      {rails.filter(r => r.items.length > 0).map((r, i) => (
        <Fragment key={r.id}>
        <RailRow title={r.title} testId={`rail-${r.id}`} prod={r.id === 'watchlist'}>
          {r.id === 'watchlist' ? r.items.map(c => <WatchlistPosterCard key={c.key} card={c} onOpen={onOpen} />) : r.items.map(c => <PosterCard key={c.key} card={c} onOpen={onOpen} own={c.tmdb_id && !c.label && !c.key.startsWith('watchlist-') ? own[c.key] : undefined}
            onLongPress={r.id === WYM_RAIL_ID && r.dismiss && onDismiss ? onDismiss : undefined} />)}
        </RailRow>
        {cw.length === 0 && i === 0 && afterFirst}
        </Fragment>
      ))}
    </div>
  );
}

export const railCss = `
.luma-rail{scrollbar-width:none;-webkit-overflow-scrolling:touch}
.luma-rail::-webkit-scrollbar{display:none}
.luma-shimmer{background:linear-gradient(90deg,rgba(255,255,255,.05) 0%,rgba(255,255,255,.11) 50%,rgba(255,255,255,.05) 100%);background-size:200% 100%;animation:lumaShimmer 1.4s ease-in-out infinite}
@keyframes lumaShimmer{from{background-position:200% 0}to{background-position:-200% 0}}
.luma-rails-in{animation:lumaRailsIn var(--m-dur-crossfade) var(--m-ease-standard) both}
.luma-skel-out{animation:lumaSkelOut var(--m-dur-crossfade) var(--m-ease-standard) both}
@keyframes lumaSkelOut{from{opacity:1}to{opacity:0}}
[data-xf-hold] .luma-rails-in,[data-xf-hold] .luma-skel-out{animation-play-state:paused}
@keyframes lumaRailsIn{from{opacity:0}to{opacity:1}}
@media (prefers-reduced-motion: reduce){.luma-shimmer{animation:none}.luma-rails-in{animation:none}}
`;

const cardButton: React.CSSProperties = {
  display: 'block', padding: 0, margin: 0, border: 0, background: 'none', color: 'inherit',
  font: 'inherit', textAlign: 'left', cursor: 'pointer', WebkitTapHighlightColor: 'transparent',
};
const railWrap: React.CSSProperties = { marginTop: 28 };
const railTitle: React.CSSProperties = {
  margin: '0 20px 12px', fontSize: 19, fontWeight: 600, letterSpacing: '-0.01em', fontFamily: FONT, color: '#fff',
};
const scroller: React.CSSProperties = {
  display: 'flex', gap: 12, overflowX: 'auto', overflowY: 'hidden', padding: '0 20px', scrollSnapType: 'x mandatory',
  scrollPaddingLeft: 20, marginTop: 12,
  // Sideways swipes scroll the rail, up/down swipes still scroll the page.
  touchAction: 'pan-x pan-y', overscrollBehaviorX: 'contain', WebkitOverflowScrolling: 'touch',
};
const posterCard: React.CSSProperties = { flex: 'none', width: 'clamp(112px, 30vw, 160px)', scrollSnapAlign: 'start' };
const posterImg: React.CSSProperties = { position: 'relative', width: '100%', aspectRatio: '2 / 3', borderRadius: 12, overflow: 'hidden' };
const wideCard: React.CSSProperties = { flex: 'none', width: 'clamp(240px, 72vw, 320px)', scrollSnapAlign: 'start' };
const wideImg: React.CSSProperties = { position: 'relative', width: '100%', aspectRatio: '16 / 9', borderRadius: 14, overflow: 'hidden' };
const fill: React.CSSProperties = { position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' };
const cardTitle: React.CSSProperties = {
  marginTop: 8, fontSize: 14, fontWeight: 500, color: 'rgba(255,255,255,0.92)', fontFamily: FONT,
  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
};
// Prod MobilePosterCard digitalReleaseLabel: 9px semibold, wide tracking, white/70, centered, mt-1.
const cardLabel: React.CSSProperties = {
  marginTop: 4, fontSize: 9, fontWeight: 600, letterSpacing: '0.025em', color: 'rgba(255,255,255,0.7)',
  textAlign: 'center', fontFamily: FONT,
};
const cardSub: React.CSSProperties = { marginTop: 2, fontSize: 12, color: 'rgba(255,255,255,0.5)', fontFamily: FONT };
const barTrack: React.CSSProperties = { position: 'absolute', left: 10, right: 10, bottom: 10, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.25)' };
const barFill: React.CSSProperties = { height: '100%', borderRadius: 2, background: '#3b82f6' };
const skel: React.CSSProperties = { borderRadius: 12 };
/** The fading copy's blocks: the shimmer's mid tone, static (no infinite animation under the 200ms fade; Reviewer). */
const GHOST_FILL: React.CSSProperties = { background: 'rgba(255,255,255,0.08)' };

/** Prod 1b4ea74's font stack (Tailwind's default sans, inherited by its home page). */
export const PROD_FONT = 'ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji"';
const wlRailWrap: React.CSSProperties = { marginTop: 28, fontFamily: PROD_FONT };
const wlRailTitle: React.CSSProperties = {
  margin: '0 12px 12px', fontSize: 18, fontWeight: 600, color: 'rgba(255,255,255,0.92)', letterSpacing: '-0.3px', fontFamily: PROD_FONT,
};
const wlScroller: React.CSSProperties = {
  display: 'flex', gap: 12, paddingLeft: 12, paddingRight: 12, overflowX: 'auto', paddingBottom: 8,
};
const wlCard: React.CSSProperties = { flexShrink: 0, width: 116, fontFamily: PROD_FONT };
const wlPoster: React.CSSProperties = {
  position: 'relative', width: 116, height: 174, borderRadius: 10, overflow: 'hidden', background: 'rgba(255,255,255,0.06)',
};
const wlText: React.CSSProperties = { marginTop: 6, width: 116 };
const wlLabel: React.CSSProperties = {
  margin: '0 0 2px', fontSize: 10, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase',
  color: 'rgba(255,255,255,0.7)', textAlign: 'center', lineHeight: 1.2,
};
const wlTitle: React.CSSProperties = {
  margin: 0, fontSize: 12, fontWeight: 500, color: 'rgba(255,255,255,0.88)', whiteSpace: 'nowrap', overflow: 'hidden',
  textOverflow: 'ellipsis', lineHeight: 1.3,
};
const wlSub: React.CSSProperties = { margin: '2px 0 0', fontSize: 10, color: 'rgba(255,255,255,0.38)' };
