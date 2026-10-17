/** Phone-first rails on Luma's purple-navy glass. */
import { img, type Card, type Rail } from './homeApi';
import { FONT } from '@/components/start/StartUI';
import { prefetchModalColors } from '@/services/modalColors';

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

/** Warm the detail popup's color blur with the ids openDetail will use. */
export function warmCard(c: Card): void {
  if (c.tmdb_id) return prefetchModalColors(c.type === 'movie' ? 'movie' : 'tv', undefined, c.tmdb_id);
  if (c.type === 'movie') return prefetchModalColors('movie', Number(c.key));
  prefetchModalColors('tv', Number(c.show_key || c.key));
}

type OnOpen = ((card: Card) => void) | undefined;

/** A card is a plain button when it can open the detail popup. */
function CardShell({ card, onOpen, style, children }: { card: Card; onOpen: OnOpen; style: React.CSSProperties; children: React.ReactNode }) {
  if (!onOpen) return <div style={style}>{children}</div>;
  return (
    <button type="button" onClick={() => onOpen(card)} aria-label={card.title}
      onPointerEnter={() => warmCard(card)} onPointerDown={() => warmCard(card)} onFocus={() => warmCard(card)}
      data-testid={`card-${card.key}`} style={{ ...cardButton, ...style }}>
      {children}
    </button>
  );
}

export function PosterCard({ card, onOpen }: { card: Card; onOpen?: OnOpen }) {
  const src = img(card.poster);
  return (
    <CardShell card={card} onOpen={onOpen} style={posterCard}>
      <div style={{ ...posterImg, ...GLASS }}>
        {src && <img src={src} alt="" loading="lazy" style={fill} draggable={false} />}
      </div>
      <div style={cardTitle}>{card.title}</div>
      {card.subtitle && <div style={cardSub}>{card.subtitle}</div>}
    </CardShell>
  );
}

export function WideCard({ card, onOpen }: { card: Card; onOpen?: OnOpen }) {
  const src = img(card.art || card.poster);
  const pct = Math.max(0, Math.min(1, card.progress || 0));
  return (
    <CardShell card={card} onOpen={onOpen} style={wideCard}>
      <div style={{ ...wideImg, ...GLASS }}>
        {src && <img src={src} alt="" loading="lazy" style={fill} draggable={false} />}
        {pct > 0 && (
          <div style={barTrack}><div data-testid="progress" style={{ ...barFill, width: `${pct * 100}%` }} /></div>
        )}
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

export function HomeRails({ cw, rails, onOpen }: { cw: Card[]; rails: Rail[]; onOpen?: (card: Card) => void }) {
  return (
    <div data-testid="home-rails" className="luma-rails-in">
      {cw.length > 0 && (
        <RailRow title="Continue watching" testId="rail-continue">
          {cw.map(c => <WideCard key={c.key} card={c} onOpen={onOpen} />)}
        </RailRow>
      )}
      {rails.filter(r => r.items.length > 0).map(r => (
        <RailRow key={r.id} title={r.title} testId={`rail-${r.id}`}>
          {r.items.map(c => <PosterCard key={c.key} card={c} onOpen={onOpen} />)}
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
