/**
 * SimilarPanel — "More Like This" for the movie detail modal.
 *
 * Mobile: bottom sheet (grab handle, drag-to-dismiss) matching ReviewsPanel.
 * Tablet/web: full-screen overlay with the same back-chevron affordance.
 *
 * Layout is two labelled poster rails — "In Your Library" first, then
 * "Not in Your Library". Rails rather than a grid because that's how every
 * other browse surface in the app reads, and because the owned set is usually
 * short: a grid of three would leave a lot of dead space above the section
 * that actually has the recommendations in it.
 *
 * Tapping a poster opens that movie's own detail modal through the shared
 * modal host, which stacks. Closing it restores this movie with Similar
 * still open (restoreSimilar), so you can keep browsing More Like This.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useUiDensity } from '@/hooks/useUiDensity';
import { useModalHost } from '@/contexts/ModalHostContext';
import { useSimilarMovies } from '@/hooks/useSimilarMovies';
import { openModalForItem } from '@/utils/modalNavigation';
import type { NormalizedMediaItem } from '@/types/media';

const DISMISS_THRESHOLD = 120;

interface SimilarPanelProps {
  tmdbId: number | null;
  ratingKey?: number | null;
  title: string;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Poster tile
// ---------------------------------------------------------------------------

function SimilarTile({
  item,
  width,
  onClick,
}: {
  item: NormalizedMediaItem;
  width: number;
  onClick: () => void;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [pressed, setPressed] = useState(false);
  const height = Math.round(width * 1.5);

  return (
    <button
      type="button"
      onClick={onClick}
      onTouchStart={() => setPressed(true)}
      onTouchEnd={() => setPressed(false)}
      onTouchCancel={() => setPressed(false)}
      style={{
        outline: 'none', background: 'none', border: 'none', padding: 0,
        cursor: 'pointer', touchAction: 'manipulation', flexShrink: 0,
        textAlign: 'left',
        transform: pressed ? 'scale(0.93)' : 'scale(1)',
        transition: pressed ? 'transform 0.1s ease-out' : 'transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)',
      }}
    >
      <div
        style={{
          width, height, borderRadius: 10, overflow: 'hidden',
          background: 'rgba(255,255,255,0.06)', position: 'relative',
        }}
      >
        {item.posterUrl && !imgError ? (
          <img
            src={item.posterUrl}
            alt={item.title}
            loading="lazy"
            draggable={false}
            onLoad={() => setImgLoaded(true)}
            onError={() => setImgError(true)}
            style={{
              width: '100%', height: '100%', objectFit: 'cover', display: 'block',
              opacity: imgLoaded ? 1 : 0, transition: 'opacity 0.3s ease',
            }}
          />
        ) : (
          <div style={{
            width: '100%', height: '100%', display: 'flex', alignItems: 'center',
            justifyContent: 'center', color: 'rgba(255,255,255,0.15)', fontSize: 11,
          }}>
            No Poster
          </div>
        )}
        {item.voteAverage != null && item.voteAverage > 0 && (
          <div style={{
            position: 'absolute', bottom: 6, right: 6, display: 'flex',
            alignItems: 'center', gap: 3, background: 'rgba(0,0,0,0.7)',
            borderRadius: 6, padding: '1px 5px',
          }}>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="#fbbf24" stroke="none">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
            <span style={{ fontSize: 10, fontWeight: 600, color: 'rgba(255,255,255,0.9)' }}>
              {item.voteAverage.toFixed(1)}
            </span>
          </div>
        )}
      </div>
      <div style={{ marginTop: 6, width }}>
        <p style={{
          margin: 0, fontSize: 12, fontWeight: 500, color: 'rgba(255,255,255,0.88)',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', lineHeight: 1.3,
        }}>
          {item.title}
        </p>
        {item.year != null && (
          <p style={{ margin: '2px 0 0', fontSize: 10, color: 'rgba(255,255,255,0.38)' }}>
            {item.year}
          </p>
        )}
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Section — heading + horizontal rail
// ---------------------------------------------------------------------------

function SimilarSection({
  heading,
  count,
  dotColor,
  items,
  tileWidth,
  onItemClick,
}: {
  heading: string;
  count: number;
  dotColor: string;
  items: NormalizedMediaItem[];
  tileWidth: number;
  onItemClick: (item: NormalizedMediaItem) => void;
}) {
  if (items.length === 0) return null;

  return (
    <section style={{ marginBottom: 26 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '0 16px', marginBottom: 12,
      }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%', background: dotColor,
          boxShadow: `0 0 8px ${dotColor}`, flexShrink: 0,
        }} />
        <h3 style={{
          margin: 0, fontSize: 16, fontWeight: 600,
          color: 'rgba(255,255,255,0.92)', letterSpacing: '-0.2px',
        }}>
          {heading}
        </h3>
        <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.35)' }}>{count}</span>
      </div>
      <div style={{
        display: 'flex', gap: 12, padding: '0 16px 4px',
        overflowX: 'auto', WebkitOverflowScrolling: 'touch',
        scrollbarWidth: 'none',
      }}>
        {items.map((item) => (
          <SimilarTile
            key={item.id}
            item={item}
            width={tileWidth}
            onClick={() => onItemClick(item)}
          />
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------

export function SimilarPanel({ tmdbId, ratingKey, title, onClose }: SimilarPanelProps) {
  const d = useUiDensity();
  const isMobile = d.mode === 'compact';
  const modalHost = useModalHost();
  const { owned, unowned, loading, error } = useSimilarMovies(tmdbId, ratingKey);

  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [dismissing, setDismissing] = useState(false);
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    if (isMobile) requestAnimationFrame(() => setEntered(true));
  }, [isMobile]);

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    setTimeout(onClose, 250);
  }, [onClose]);

  // Drag from the handle ONLY. The usual "or the scroll area is at the top"
  // condition is wrong for this sheet: its content is short and laid out as
  // horizontal rails, so scrollTop is 0 nearly always and every swipe over a
  // poster — including the vertical drift in a horizontal flick — grabbed the
  // whole sheet and made it bounce.
  const onSheetTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (!target.closest('[data-sheet-handle]') || e.touches.length === 0) return;
    dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
  }, []);

  const onSheetTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || e.touches.length === 0) return;
    const y = e.touches[0]!.clientY;
    if (y - dragRef.current.startY > 0) {
      dragRef.current.currentY = y;
      setDragOffset(y - dragRef.current.startY);
    }
  }, []);

  const onSheetTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) handleDismiss();
    else setDragOffset(0);
  }, [handleDismiss]);

  // ESC closes the panel, not the modal underneath it.
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return;
      e.stopPropagation();
      if (isMobile) handleDismiss();
      else onClose();
    }
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [onClose, isMobile, handleDismiss]);

  // Opening the picked movie replaces this modal with its own (the host stacks
  // the current one), so the panel tears down with its parent — no need to
  // close it first.
  const handleItemClick = useCallback((item: NormalizedMediaItem) => {
    openModalForItem(modalHost, item, 'similar_movies');
  }, [modalHost]);

  const tileWidth = isMobile ? 116 : 150;
  const hasAny = owned.length > 0 || unowned.length > 0;

  const body = (
    <>
      {loading && (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', gap: 20, padding: '60px 0 80px',
        }}>
          <div style={{
            width: 36, height: 36, borderRadius: '50%',
            border: '3px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)',
            animation: 'similarSpin 0.8s linear infinite',
          }} />
          <span style={{ fontSize: 14, color: 'rgba(255,255,255,0.5)' }}>
            Finding similar movies...
          </span>
          <style>{`@keyframes similarSpin { to { transform: rotate(360deg) } }`}</style>
        </div>
      )}

      {!loading && error && (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          gap: 8, padding: '60px 24px 80px', textAlign: 'center',
        }}>
          <span style={{ fontSize: 15, fontWeight: 500, color: 'rgba(255,255,255,0.5)' }}>
            Couldn't load similar movies
          </span>
          <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.3)' }}>
            Check your connection and try again
          </span>
        </div>
      )}

      {!loading && !error && !hasAny && (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          gap: 8, padding: '60px 24px 80px', textAlign: 'center',
        }}>
          <span style={{ fontSize: 15, fontWeight: 500, color: 'rgba(255,255,255,0.5)' }}>
            No similar movies found
          </span>
          <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.3)' }}>
            No related titles found for {title}
          </span>
        </div>
      )}

      {!loading && !error && hasAny && (
        <>
          <SimilarSection
            heading="In Library"
            count={owned.length}
            dotColor="#4ade80"
            items={owned}
            tileWidth={tileWidth}
            onItemClick={handleItemClick}
          />
          <SimilarSection
            heading="Related"
            count={unowned.length}
            dotColor="rgba(255,255,255,0.35)"
            items={unowned}
            tileWidth={tileWidth}
            onItemClick={handleItemClick}
          />
        </>
      )}
    </>
  );

  // =========================================================================
  // MOBILE: bottom sheet
  // =========================================================================
  if (isMobile) {
    const sheetTranslate = dismissing ? '100%' : entered ? `${dragOffset}px` : '100%';

    return (
      <div
        data-similar-panel
        role="dialog"
        aria-modal="true"
        aria-label="Similar movies"
        style={{
          position: 'fixed', inset: 0, zIndex: 200,
          display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
          background: entered && !dismissing ? 'rgba(0,0,0,0.4)' : 'transparent',
          transition: 'background 0.25s ease',
        }}
        onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
      >
        <div
          onTouchStart={onSheetTouchStart}
          onTouchMove={onSheetTouchMove}
          onTouchEnd={onSheetTouchEnd}
          style={{
            maxHeight: 'calc(100% - 60px)',
            display: 'flex', flexDirection: 'column',
            background: 'var(--color-kiosk-black)',
            borderRadius: '20px 20px 0 0',
            overflow: 'hidden',
            transform: `translateY(${sheetTranslate})`,
            transition: dragOffset > 0 ? 'none' : 'transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)',
            willChange: 'transform',
          }}
        >
          {/* Grab handle */}
          <div
            data-sheet-handle
            style={{
              flexShrink: 0, display: 'flex', justifyContent: 'center',
              alignItems: 'center',
              // Taller than it looks: the handle is now the only way to drag
              // the sheet, so it needs a real touch target, not a 5px bar.
              height: 34, cursor: 'grab', touchAction: 'none',
            }}
          >
            <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
          </div>

          <h2 style={{
            flexShrink: 0, margin: '0 0 16px', padding: '0 16px',
            fontSize: 20, fontWeight: 600, color: 'rgba(255,255,255,0.95)',
            letterSpacing: '-0.3px',
          }}>
            More Like This
          </h2>

          <div
            style={{
              flex: 1, minHeight: 0, overflowY: 'auto',
              WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y',
              paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 16px)',
            }}
          >
            {body}
          </div>
        </div>
      </div>
    );
  }

  // =========================================================================
  // TABLET / WEB: full-screen overlay
  // =========================================================================
  return (
    <div
      data-similar-panel
      role="dialog"
      aria-modal="true"
      aria-label="Similar movies"
      style={{
        position: 'fixed', inset: 0, zIndex: 200,
        display: 'flex', flexDirection: 'column',
        background: 'rgba(0,0,0,0.95)',
      }}
    >
      <button
        type="button"
        onClick={onClose}
        aria-label="Close similar movies"
        style={{
          position: 'absolute', top: 20, left: 20, zIndex: 10,
          width: 72, height: 72, borderRadius: '50%',
          border: '1px solid rgba(255,255,255,0.15)',
          background: d.isTablet ? 'rgba(20,20,20,0.85)' : 'rgba(20,20,20,0.7)',
          backdropFilter: d.isTablet ? 'none' : 'blur(20px) saturate(1.8)',
          WebkitBackdropFilter: d.isTablet ? 'none' : 'blur(20px) saturate(1.8)',
          color: 'rgba(255,255,255,0.9)', cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
        }}
      >
        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>

      <div data-scroll-container className="luma-scroll-y" style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '112px 24px 40px', WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}>
        <div style={{ maxWidth: 1280, margin: '0 auto' }}>
          <h2 style={{
            margin: '0 0 24px', padding: '0 16px',
            fontSize: 28, fontWeight: 600, color: 'rgba(255,255,255,0.95)',
            letterSpacing: '-0.5px',
          }}>
            More Like This
          </h2>
          {body}
        </div>
      </div>
    </div>
  );
}
