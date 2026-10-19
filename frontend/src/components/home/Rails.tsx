/** Phone-first rails on Luma's purple-navy glass. */
import { img, type Card, type Rail } from './homeApi';
import { FONT } from '@/components/start/StartUI';
import { useEffect, useRef } from 'react';
import { scheduleWarm, warmDetail } from '@/services/modalPrefetch';
import { OwnedOrb, useRailOwnership, type CardOwnership } from './railOwnership';

export const GLASS: React.CSSProperties = {
  background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)',
};

export function RailRow({ title, children, testId }: { title: string; children: React.ReactNode; testId?: string }) {
  return (
    <section style={railWrap} data-testid={testId}>
      <h2 style={railTitle}>{title}</h2>
      <div style={scroller} className="luma-rail">{children}</div>
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
      cancel = e?.isIntersecting ? scheduleWarm(warmKey(card), () => warmCard(card)) : () => {};
    }, { rootMargin: '150px' });
    io.observe(el);
    return () => { io.disconnect(); cancel(); };
  }, [ref, card, on]);
}

/** A card is a plain button when it can open the detail popup. */
function CardShell({ card, onOpen, style, children }: { card: Card; onOpen: OnOpen; style: React.CSSProperties; children: React.ReactNode }) {
  const ref = useRef<HTMLButtonElement>(null);
  useWarmWhenVisible(ref, card, !!onOpen);
  if (!onOpen) return <div style={style}>{children}</div>;
  const warm = () => { void warmCard(card); };
  return (
    <button ref={ref} type="button" onClick={(e) => onOpen(card, e.currentTarget)} aria-label={card.title}
      onPointerEnter={warm} onPointerDown={warm} onTouchStart={warm} onFocus={warm}
      data-testid={`card-${card.key}`} className="luma-pressable" style={{ ...cardButton, ...style }}>
      {children}
    </button>
  );
}

export function PosterCard({ card, onOpen, own }: { card: Card; onOpen?: OnOpen; own?: CardOwnership }) {
  const src = img(card.poster);
  return (
    <CardShell card={card} onOpen={onOpen} style={posterCard}>
      <div style={{ ...posterImg, ...GLASS }}>
        {src && <img src={src} alt="" loading="lazy" style={fill} draggable={false} />}
        <OwnedOrb own={own} />
      </div>
      <div style={cardTitle}>{card.title}</div>
      {card.subtitle && <div style={cardSub}>{card.subtitle}</div>}
    </CardShell>
  );
}

export function WideCard({ card, onOpen, own }: { card: Card; onOpen?: OnOpen; own?: CardOwnership }) {
  const src = img(card.art || card.poster);
  const pct = Math.max(0, Math.min(1, card.progress || 0));
  return (
    <CardShell card={card} onOpen={onOpen} style={wideCard}>
      <div style={{ ...wideImg, ...GLASS }}>
        {src && <img src={src} alt="" loading="lazy" style={fill} draggable={false} />}
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

export function SkeletonRails({ rails = 3 }: { rails?: number }) {
  return (
    <div data-testid="home-skeleton" aria-busy="true">
      {Array.from({ length: rails }, (_, r) => (
        <section key={r} style={railWrap}>
          <div className="luma-shimmer" style={{ ...skel, width: 140, height: 18, borderRadius: 6, marginLeft: 20 }} />
          <div style={scroller}>
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} style={r === 0 ? wideCard : posterCard}>
                <div className="luma-shimmer" style={{ ...skel, ...(r === 0 ? wideImg : posterImg) }} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export function HomeRails({ cw, rails, onOpen }: { cw: Card[]; rails: Rail[]; onOpen?: (card: Card, el?: HTMLElement | null) => void }) {
  // v181: prod's rule: no orb on all-owned library rails (Continue watching, every Plex library rail);
  // the orb only on mixed rails (Trakt), resolved by prod's batch check.
  const own = useRailOwnership(rails.flatMap(r => r.items).filter(c => c.tmdb_id));
  return (
    <div data-testid="home-rails" className="luma-rails-in">
      {cw.length > 0 && (
        <RailRow title="Continue watching" testId="rail-continue">
          {cw.map(c => <WideCard key={c.key} card={c} onOpen={onOpen} />)}
        </RailRow>
      )}
      {rails.filter(r => r.items.length > 0).map(r => (
        <RailRow key={r.id} title={r.title} testId={`rail-${r.id}`}>
          {r.items.map(c => <PosterCard key={c.key} card={c} onOpen={onOpen} own={c.tmdb_id ? own[c.key] : undefined} />)}
        </RailRow>
      ))}
    </div>
  );
}

export const railCss = `
.luma-rail{scrollbar-width:none;-webkit-overflow-scrolling:touch}
.luma-rail::-webkit-scrollbar{display:none}
.luma-shimmer{background:linear-gradient(90deg,rgba(255,255,255,.05) 0%,rgba(255,255,255,.11) 50%,rgba(255,255,255,.05) 100%);background-size:200% 100%;animation:lumaShimmer 1.4s ease-in-out infinite}
@keyframes lumaShimmer{from{background-position:200% 0}to{background-position:-200% 0}}
.luma-rails-in{animation:lumaRailsIn .3s ease both}
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
const cardSub: React.CSSProperties = { marginTop: 2, fontSize: 12, color: 'rgba(255,255,255,0.5)', fontFamily: FONT };
const barTrack: React.CSSProperties = { position: 'absolute', left: 10, right: 10, bottom: 10, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.25)' };
const barFill: React.CSSProperties = { height: '100%', borderRadius: 2, background: '#3b82f6' };
const skel: React.CSSProperties = { borderRadius: 12 };
