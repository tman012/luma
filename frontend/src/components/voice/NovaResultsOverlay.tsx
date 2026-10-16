/**
 * NovaResultsOverlay — visual results panel for voice queries.
 *
 * Sized like MediaDetailModal (centered card, max ~1500px, 90vh) so it
 * doesn't dominate the screen. Tap a tile → closes Nova synchronously
 * via flushSync, then opens the detail modal on top.
 *
 * z-index sits BELOW MediaDetailModal (z-200) so subsequent detail modals
 * always layer correctly.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { flushSync } from 'react-dom';
import { useModalHost } from '@/contexts/ModalHostContext';
import type { NovaResultItem } from '@/types/voice';
import { cn } from '@/utils/cn';

interface Props {
  open: boolean;
  title: string;
  items: NovaResultItem[];
  onClose: () => void;
}

type Phase = 'entering' | 'visible' | 'exiting' | 'gone';

export function NovaResultsOverlay({ open, title, items, onClose }: Props) {
  const { openMovie, openTv } = useModalHost();
  const [phase, setPhase] = useState<Phase>('gone');
  const overlayRef = useRef<HTMLDivElement>(null);

  // Phase machine driven by `open`
  useEffect(() => {
    if (open) {
      setPhase('entering');
      const f = requestAnimationFrame(() => setPhase('visible'));
      return () => cancelAnimationFrame(f);
    }
    if (phase === 'visible' || phase === 'entering') {
      setPhase('exiting');
      const t = setTimeout(() => setPhase('gone'), 220);
      return () => clearTimeout(t);
    }
    return undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Body scroll lock while visible
  useEffect(() => {
    if (phase === 'gone') return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [phase]);

  // ESC closes
  useEffect(() => {
    if (phase === 'gone') return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [phase, onClose]);

  const handleCardClick = (item: NovaResultItem) => {
    const opts: Record<string, unknown> = { origin: 'nova_results' };
    if (item.tmdb_id) opts.tmdbId = item.tmdb_id;
    if (item.rating_key) opts.ratingKey = item.rating_key;
    if (!item.tmdb_id && !item.rating_key) return; // No way to open

    // Close Nova synchronously so the detail modal doesn't stack on top of us
    flushSync(() => {
      onClose();
    });
    if (item.media_type === 'tv') openTv(opts as any);
    else openMovie(opts as any);
  };

  if (phase === 'gone') return null;

  const visible = phase === 'visible';
  const cardScale = visible ? 'scale(1)' : 'scale(0.96)';
  const overlayOpacity = visible ? 1 : 0;

  const node = (
    <div
      ref={overlayRef}
      onClick={(e) => {
        if (e.target === overlayRef.current) onClose();
      }}
      style={{
        position: 'fixed',
        inset: 0,
        // Below MediaDetailModal (z-200) so stacking on tile-tap is correct
        zIndex: 150,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        opacity: overlayOpacity,
        transition: 'opacity 0.22s ease',
        padding: '20px',
      }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        style={{
          position: 'relative',
          width: '100%',
          maxWidth: '1500px',
          height: '90vh',
          borderRadius: '20px',
          overflow: 'hidden',
          backgroundColor: '#141414',
          boxShadow: '0 32px 100px rgba(0, 0, 0, 0.8)',
          transform: cardScale,
          opacity: overlayOpacity,
          transition: visible
            ? 'transform 0.4s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.4s cubic-bezier(0.16, 1, 0.3, 1)'
            : 'transform 0.22s ease, opacity 0.22s ease',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between gap-4 border-b border-white/10 px-6 py-4 md:px-8 md:py-5"
          style={{ background: 'rgba(20, 20, 20, 0.95)' }}
        >
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-white/45">
              Nova
            </p>
            <h2 className="mt-0.5 truncate text-lg font-semibold text-white md:text-xl">
              {title}
            </h2>
            <p className="mt-0.5 text-xs text-white/50">
              {items.length} {items.length === 1 ? 'result' : 'results'}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/5 text-white/80 transition hover:bg-white/10 hover:text-white md:h-10 md:w-10"
            aria-label="Close results"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Grid */}
        <div data-scroll-container className="luma-scroll-y flex-1 px-5 py-6 md:px-8 md:py-7">
          {items.length === 0 ? (
            <div className="flex h-full items-center justify-center text-white/60">
              No results.
            </div>
          ) : (
            <div
              className="grid gap-5 md:gap-6"
              style={{
                gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
              }}
            >
              {items.map((item, idx) => (
                <NovaResultCard
                  key={`${item.tmdb_id ?? item.rating_key ?? 'na'}-${idx}`}
                  item={item}
                  onClick={() => handleCardClick(item)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );

  return createPortal(node, document.body);
}

function NovaResultCard({
  item,
  onClick,
}: {
  item: NovaResultItem;
  onClick: () => void;
}) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const [imgError, setImgError] = useState(false);

  const imageUrl = useMemo(() => {
    const raw = item.poster_url || item.backdrop_url || null;
    if (!raw) return null;
    if (raw.startsWith('/api/') || raw.startsWith('http')) return raw;
    if (raw.startsWith('/')) return `/api/img/tmdb?path=${encodeURIComponent(raw)}&size=w500`;
    return raw;
  }, [item.poster_url, item.backdrop_url]);

  const orbColor = item.owned ? '#22c55e' : item.in_watchlist ? '#3b82f6' : null;
  const hasHandle = !!(item.tmdb_id || item.rating_key);

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!hasHandle}
      className={cn(
        'group relative text-left outline-none focus-visible:ring-2 focus-visible:ring-white/40 rounded-xl',
        hasHandle ? 'cursor-pointer' : 'cursor-default opacity-80',
      )}
    >
      <div
        className="relative aspect-[2/3] overflow-hidden rounded-xl border border-white/10 transition-transform duration-200 group-hover:scale-[1.03]"
        style={{ background: 'rgba(255, 255, 255, 0.04)' }}
      >
        {imageUrl && !imgError ? (
          <img
            src={imageUrl}
            alt={item.title}
            loading="lazy"
            draggable={false}
            className={cn(
              'h-full w-full object-cover transition-opacity duration-300',
              imgLoaded ? 'opacity-100' : 'opacity-0',
            )}
            onLoad={() => setImgLoaded(true)}
            onError={() => setImgError(true)}
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-3 text-center">
            <span className="text-[13px] font-medium leading-tight text-white/70">
              {item.title}
            </span>
            {item.year != null && (
              <span className="text-[11px] text-white/40">{item.year}</span>
            )}
          </div>
        )}

        <div className="absolute inset-0 bg-gradient-to-t from-black/70 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />

        {orbColor && (
          <div
            className="absolute left-3 top-3 h-3 w-3 rounded-full"
            style={{ background: orbColor, boxShadow: `0 0 10px ${orbColor}aa` }}
          />
        )}

        {item.rating != null && item.rating > 0 && !item.owned && (
          <div className="absolute bottom-2 right-2 flex items-center gap-1 rounded-md bg-black/70 px-2 py-0.5 backdrop-blur-sm">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="#fbbf24" stroke="none">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
            <span className="text-[11px] font-semibold text-white/90">
              {item.rating.toFixed(1)}
            </span>
          </div>
        )}

        {item.seeders != null && (
          <div className="absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/70 px-2 py-0.5 text-[11px] font-medium text-white backdrop-blur-sm">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
              <path d="M12 2v20M5 9l7-7 7 7" />
            </svg>
            {item.seeders >= 1000 ? `${(item.seeders / 1000).toFixed(1)}K` : item.seeders}
          </div>
        )}
      </div>

      <div className="mt-2.5 min-h-[3.5rem] px-0.5">
        <p className="truncate text-[15px] font-medium text-white/95">{item.title}</p>
        {item.year != null && (
          <p className="text-[12px] text-white/50">
            {item.year}
            {item.resolution ? ` · ${item.resolution}` : ''}
            {item.tracker ? ` · ${item.tracker}` : ''}
          </p>
        )}
        {item.subtitle && (
          <p className="truncate text-[12px] text-white/50">{item.subtitle}</p>
        )}
      </div>
    </button>
  );
}
