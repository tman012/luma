/**
 * TopReviews — Plex-style "Ratings & Reviews" row + full-review sheet.
 *
 * Data: GET /api/reviews/top?title=&year= (app/rt_top_reviews.py) — Rotten
 * Tomatoes critic reviews (Top critics first), falling back to RT audience
 * reviews (verified first) when a title has no critic reviews. Cached
 * server-side. Ratings arrive normalised to 0-10 → 5 stars with halves
 * (critic "3/4" = 7.5 → 3.5★); critics without a score show Fresh/Rotten.
 *
 * The row hides itself when there are no reviews. Cards scroll horizontally
 * with snap (inner scroll only — the page never pans sideways) and are
 * DPAD-focusable in TV mode.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useFocusable } from '@/hooks/useFocusable';
import { RATING_ICONS } from '@/utils/ratingIcons';
import { apiFetch } from '../../apiClient';

export interface TopReview {
  id: string;
  kind: 'critic' | 'audience';
  username: string; // critic or audience display name
  display_name: string | null;
  publication: string | null; // critics only
  avatar: string | null;
  date: string | null;
  rating: number | null; // 0-10 (null = critic gave no score)
  score_text: string | null; // critic's original score, e.g. "3/4", "B+"
  fresh: boolean | null; // critics: Fresh / Rotten
  top_critic: boolean;
  verified: boolean; // audience: verified ticket buyer
  message: string;
  url: string | null; // full review on the publication's site
  has_spoilers: boolean;
}

type Variant = 'compact' | 'expanded';

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

export function useTopReviews(title?: string | null, year?: number | null, mediaType: 'movie' | 'tv' = 'movie') {
  const [reviews, setReviews] = useState<TopReview[]>([]);
  useEffect(() => {
    setReviews([]);
    if (!title) return;   // v217: minimal builds fetch reviews too (prod 1b4ea74's 'Cast & Reviews')
    const controller = new AbortController();
    const params = new URLSearchParams({ title });
    if (year) params.set('year', String(year));
    if (mediaType === 'tv') params.set('media_type', 'tv');
    apiFetch(`/api/reviews/top?${params}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d && Array.isArray(d.reviews)) setReviews(d.reviews); })
      .catch(() => {});
    return () => controller.abort();
  }, [title, year, mediaType]);
  return reviews;
}

// ---------------------------------------------------------------------------
// Bits
// ---------------------------------------------------------------------------

function fmtDate(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
}

function displayName(r: TopReview): string {
  return r.display_name || r.username;
}

const STAR_PATH = 'M12 2.5l2.94 5.96 6.58.96-4.76 4.64 1.12 6.55L12 17.52l-5.88 3.09 1.12-6.55L2.48 9.42l6.58-.96L12 2.5z';

function Star({ fill, size }: { fill: 0 | 0.5 | 1; size: number }) {
  const id = useRef(`hs-${Math.random().toString(36).slice(2, 9)}`).current;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      {fill === 0.5 && (
        <defs>
          <linearGradient id={id}>
            <stop offset="50%" stopColor="currentColor" />
            <stop offset="50%" stopColor="transparent" />
          </linearGradient>
        </defs>
      )}
      <path
        d={STAR_PATH}
        fill={fill === 1 ? 'currentColor' : fill === 0.5 ? `url(#${id})` : 'none'}
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Stars({ rating, size = 15 }: { rating: number | null; size?: number }) {
  if (rating == null) return null;
  const halves = Math.round(Math.max(0, Math.min(10, rating))); // 0-10 → half-star units
  return (
    <div
      style={{ display: 'flex', gap: Math.round(size * 0.2), color: 'rgba(255,255,255,0.92)' }}
      aria-label={`${(halves / 2).toFixed(1).replace('.0', '')} out of 5 stars`}
    >
      {[0, 1, 2, 3, 4].map((i) => {
        const f = halves - i * 2;
        return <Star key={i} size={size} fill={f >= 2 ? 1 : f === 1 ? 0.5 : 0} />;
      })}
    </div>
  );
}

const AVATAR_COLORS = ['#e11d48', '#7c3aed', '#2563eb', '#0891b2', '#059669', '#d97706', '#db2777', '#4f46e5'];

function Avatar({ review, size }: { review: TopReview; size: number }) {
  const [failed, setFailed] = useState(false);
  const name = displayName(review);
  const initial = (name.trim()[0] || '?').toUpperCase();
  const color = AVATAR_COLORS[(name.charCodeAt(0) || 0) % AVATAR_COLORS.length];
  if (review.avatar && !failed) {
    return (
      <img
        src={review.avatar}
        alt=""
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
        style={{ width: size, height: size, borderRadius: '50%', objectFit: 'cover', flexShrink: 0, background: 'rgba(255,255,255,0.1)' }}
      />
    );
  }
  return (
    <div
      style={{
        width: size, height: size, borderRadius: '50%', flexShrink: 0, background: color,
        color: '#fff', fontWeight: 700, fontSize: Math.round(size * 0.45),
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      {initial}
    </div>
  );
}

/** Line under the name: publication for critics, audience type otherwise. */
function subtitle(r: TopReview): string {
  if (r.kind === 'critic') return [r.publication, r.top_critic ? 'Top critic' : null].filter(Boolean).join(' · ');
  return r.verified ? 'Verified audience' : 'Audience review';
}

function ReviewHeader({ review, avatar, nameSize }: { review: TopReview; avatar: number; nameSize: number }) {
  const sub = subtitle(review);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
      <Avatar review={review} size={avatar} />
      <div style={{ minWidth: 0, textAlign: 'left' }}>
        <div style={{ fontSize: nameSize, fontWeight: 600, color: '#fff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {displayName(review)}
        </div>
        {sub && (
          <div style={{ fontSize: nameSize - 2, color: 'rgba(255,255,255,0.72)', marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {sub}
          </div>
        )}
      </div>
    </div>
  );
}

/** Small Fresh tomato / Rotten splat (RT's own icons, as in the ratings bar). */
function FreshIcon({ fresh, size }: { fresh: boolean; size: number }) {
  return (
    <img
      src={fresh ? RATING_ICONS.rtCriticFresh : RATING_ICONS.rtCriticRotten}
      alt={fresh ? 'Fresh' : 'Rotten'}
      title={fresh ? 'Fresh' : 'Rotten'}
      style={{ width: size, height: size, objectFit: 'contain', flexShrink: 0 }}
    />
  );
}

/**
 * Rating line: [tomato/splat] [stars] … date. Critics with no score show the
 * Fresh/Rotten icon + word instead of stars (never fake stars).
 */
function RatingLine({ review, starSize, fontSize, showScoreText }: { review: TopReview; starSize: number; fontSize: number; showScoreText?: boolean }) {
  const date = fmtDate(review.date);
  const hasFresh = review.kind === 'critic' && review.fresh != null;
  const extra = showScoreText && review.kind === 'critic' && review.score_text ? review.score_text : null;
  if (review.rating == null && !hasFresh && !date) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
      {hasFresh && <FreshIcon fresh={!!review.fresh} size={Math.round(starSize * 1.1)} />}
      {review.rating != null ? (
        <Stars rating={review.rating} size={starSize} />
      ) : hasFresh ? (
        <span style={{ fontSize, fontWeight: 600, color: 'rgba(255,255,255,0.85)' }}>{review.fresh ? 'Fresh' : 'Rotten'}</span>
      ) : null}
      {extra && <span style={{ fontSize, color: 'rgba(255,255,255,0.7)' }}>{extra}</span>}
      {date && (
        <span style={{ marginLeft: 'auto', paddingLeft: 6, fontSize, color: 'rgba(255,255,255,0.7)', whiteSpace: 'nowrap' }}>{date}</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card + row
// ---------------------------------------------------------------------------

function ReviewCard({ review, index, variant, onOpen }: { review: TopReview; index: number; variant: Variant; onOpen: (r: TopReview) => void }) {
  const open = useCallback(() => onOpen(review), [onOpen, review]);
  const { ref, focused, focusProps } = useFocusable({ focusKey: `mm-review-${index}`, zoneId: 'movie-detail-modal', onEnter: open });
  const elRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (focused) elRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
  }, [focused]);
  const big = variant === 'expanded';
  return (
    <button
      ref={(n) => { elRef.current = n; ref(n); }}
      {...focusProps}
      type="button"
      data-review-card
      onClick={open}
      style={{
        flexShrink: 0,
        width: big ? 340 : 'min(76vw, 272px)',
        scrollSnapAlign: 'start',
        display: 'flex',
        flexDirection: 'column',
        gap: big ? 12 : 10,
        padding: big ? '18px 18px 20px' : '14px 14px 16px',
        borderRadius: big ? 16 : 14,
        // Semi-opaque slate-navy tint + light blur: some backdrop shows through, text stays readable on light backdrops
        background: focused ? 'rgba(68,62,112,0.80)' : 'rgba(44,40,80,0.62)',
        backdropFilter: 'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
        border: focused ? '2px solid rgba(255,255,255,0.95)' : '1px solid rgba(255,255,255,0.14)',
        boxShadow: '0 2px 8px rgba(0,0,0,0.22)',
        color: 'inherit',
        textAlign: 'left',
        cursor: 'pointer',
        font: 'inherit',
        WebkitTapHighlightColor: 'transparent',
        transition: 'background 0.15s, border-color 0.15s',
      }}
    >
      <ReviewHeader review={review} avatar={big ? 40 : 34} nameSize={big ? 16 : 14} />
      <RatingLine review={review} starSize={big ? 18 : 15} fontSize={big ? 14 : 12} />
      {review.has_spoilers ? (
        <p style={{ margin: 0, fontSize: big ? 15 : 13.5, fontStyle: 'italic', color: 'rgba(255,255,255,0.68)' }}>
          Contains spoilers · tap to read
        </p>
      ) : (
        <p
          style={{
            margin: 0,
            fontSize: big ? 16 : 14,
            lineHeight: 1.45,
            color: 'rgba(255,255,255,0.92)',
            display: '-webkit-box',
            WebkitLineClamp: big ? 4 : 3,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
            whiteSpace: 'pre-line',
            overflowWrap: 'anywhere',
          }}
        >
          {review.message}
        </p>
      )}
    </button>
  );
}

export function TopReviewsRow({
  reviews,
  variant,
  onOpen,
  sidePadding,
}: {
  reviews: TopReview[];
  variant: Variant;
  onOpen: (r: TopReview) => void;
  /** Horizontal inset matching the surrounding section (px). */
  sidePadding?: number;
}) {
  if (reviews.length === 0) return null;
  const big = variant === 'expanded';
  const pad = sidePadding ?? (big ? 24 : 16);
  return (
    <div data-reviews-section style={{ marginBottom: big ? 28 : 20, textAlign: 'left' }}>
      <h2
        style={{
          fontSize: big ? 22 : 18,
          fontWeight: 600,
          color: '#fff',
          margin: big ? '0 0 12px' : '0 0 10px',
          padding: `0 ${pad}px`,
          textShadow: '0 1px 4px rgba(0,0,0,0.5)',
        }}
      >
        Ratings &amp; Reviews
      </h2>
      <div
        data-reviews-row
        style={{
          display: 'flex',
          alignItems: 'stretch',
          gap: big ? 16 : 12,
          padding: `4px ${pad}px 10px`,
          overflowX: 'auto',
          overflowY: 'hidden',
          scrollSnapType: 'x mandatory',
          scrollPaddingLeft: pad,
          WebkitOverflowScrolling: 'touch',
          overscrollBehaviorX: 'contain',
          scrollbarWidth: 'none',
        }}
      >
        {reviews.map((r, i) => (
          <ReviewCard key={r.id || i} review={r} index={i} variant={variant} onOpen={onOpen} />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Full review sheet (Luma theme: kiosk-black sheet)
// ---------------------------------------------------------------------------

export function ReviewSheet({ review, variant, onClose }: { review: TopReview; variant: Variant; onClose: () => void }) {
  const isCompact = variant === 'compact';
  const [entered, setEntered] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [dragOffset, setDragOffset] = useState(0);
  const [revealed, setRevealed] = useState(!review.has_spoilers);
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });

  useEffect(() => {
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    window.setTimeout(onClose, 250);
  }, [onClose]);

  // ESC closes this sheet only (system back is wired by the host layout's
  // registerBackHandler so it can't clobber the layout's own handler).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return;
      e.stopImmediatePropagation();
      e.preventDefault();
      handleDismiss();
    }
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [handleDismiss]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const t = e.target as HTMLElement;
    if (!t.closest('[data-sheet-handle]') || e.touches.length === 0) return;
    const y = e.touches[0]!.clientY;
    dragRef.current = { active: true, startY: y, currentY: y };
  }, []);
  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || e.touches.length === 0) return;
    const y = e.touches[0]!.clientY;
    if (y > dragRef.current.startY) { dragRef.current.currentY = y; setDragOffset(y - dragRef.current.startY); }
  }, []);
  const onTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > 90) handleDismiss(); else setDragOffset(0);
  }, [handleDismiss]);

  const visible = entered && !dismissing;
  const zoom = isCompact ? 1 : 1.25;

  const inner = (
    <>
      <div style={{ position: 'relative', flexShrink: 0, padding: isCompact ? '0 60px 14px 16px' : '22px 64px 16px 24px', zoom }}>
        <ReviewHeader review={review} avatar={42} nameSize={16} />
        <button
          type="button"
          onClick={handleDismiss}
          aria-label="Close review"
          style={{
            position: 'absolute', right: isCompact ? 14 : 18, top: isCompact ? 4 : 24,
            width: 32, height: 32, borderRadius: '50%', border: 'none',
            background: 'rgba(255,255,255,0.06)', color: 'rgba(255,255,255,0.7)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div
        className="luma-scroll-y"
        style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', overscrollBehavior: 'contain', touchAction: 'pan-y', WebkitOverflowScrolling: 'touch' }}
      >
        <div style={{ padding: isCompact ? '0 16px' : '0 24px', zoom }}>
          <div style={{ marginBottom: 14 }}>
            <RatingLine review={review} starSize={20} fontSize={13} showScoreText />
          </div>
          {revealed ? (
            <p style={{ margin: 0, fontSize: 16, lineHeight: 1.6, color: 'rgba(255,255,255,0.9)', whiteSpace: 'pre-line', overflowWrap: 'anywhere', userSelect: 'text', WebkitUserSelect: 'text' }}>
              {review.message}
            </p>
          ) : (
            <button
              type="button"
              onClick={() => setRevealed(true)}
              style={{
                width: '100%', padding: '18px 16px', borderRadius: 12, border: '1px solid rgba(255,255,255,0.12)',
                background: 'rgba(255,255,255,0.06)', color: 'rgba(255,255,255,0.85)', fontSize: 15, fontWeight: 600, cursor: 'pointer',
              }}
            >
              Contains spoilers — tap to show
            </button>
          )}
          {review.url && (
            <a
              href={review.url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 16, fontSize: 14, fontWeight: 600, color: '#4ade80', textDecoration: 'none' }}
            >
              Read full review
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M7 17L17 7" />
                <path d="M8 7h9v9" />
              </svg>
            </a>
          )}
          <div style={{ marginTop: 18, fontSize: 12, color: 'rgba(255,255,255,0.35)' }}>
            Review from Rotten Tomatoes · {review.kind === 'critic' ? (review.top_critic ? 'Top critic' : 'Critic') : review.verified ? 'Verified audience' : 'Audience'}
          </div>
          <div style={{ height: isCompact ? 'calc(env(safe-area-inset-bottom, 0px) + 20px)' : 26 }} />
        </div>
      </div>
    </>
  );

  const content = isCompact ? (
    <div className="luma-fullscreen luma-bleed"
      data-review-sheet
      role="dialog"
      aria-modal="true"
      aria-label={`Review by ${displayName(review)}`}
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 10060, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
        background: visible ? 'rgba(0,0,0,0.45)' : 'transparent', transition: 'background 0.25s ease',
      }}
    >
      <div className="luma-bleed-panel"
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          maxHeight: 'calc(100% - max(env(safe-area-inset-top, 0px), 20px) - 24px)',
          display: 'flex', flexDirection: 'column', background: 'var(--color-kiosk-black, #15102a)',
          borderRadius: '20px 20px 0 0', boxShadow: '0 -12px 40px rgba(0,0,0,0.45), var(--luma-bleed-shadow)', overflow: 'hidden',
          transform: `translateY(${dismissing ? '100%' : entered ? `${dragOffset}px` : '100%'})`,
          transition: dragOffset > 0 ? 'none' : 'transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)',
          willChange: 'transform',
        }}
      >
        <div data-sheet-handle style={{ flexShrink: 0, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', touchAction: 'none', cursor: 'grab' }}>
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>
        {inner}
      </div>
    </div>
  ) : (
    <div className="luma-fullscreen luma-bleed"
      data-review-sheet
      role="dialog"
      aria-modal="true"
      aria-label={`Review by ${displayName(review)}`}
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 10060, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
        background: visible ? 'rgba(0,0,0,0.6)' : 'transparent', transition: 'background 0.2s ease',
      }}
    >
      <div
        style={{
          width: '100%', maxWidth: 700, maxHeight: 'min(80vh, 860px)', display: 'flex', flexDirection: 'column',
          background: 'var(--color-kiosk-black, #15102a)', borderRadius: 20, border: '1px solid rgba(255,255,255,0.08)',
          boxShadow: '0 24px 80px rgba(0,0,0,0.6)', overflow: 'hidden',
          opacity: visible ? 1 : 0, transform: visible ? 'scale(1)' : 'scale(0.97)', transition: 'opacity 0.2s ease, transform 0.2s ease',
        }}
      >
        {inner}
      </div>
    </div>
  );

  return createPortal(content, document.body);
}
