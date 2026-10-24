/**
 * RateCompactSheet — the verdict prompt on a phone.
 *
 * Bottom sheet on the ActivitySheet/DownloadsSheet shell: grab-handle
 * drag-to-dismiss, tap-outside to close, scrollable body, safe-area inset. This
 * is the layout the push notification lands on, so it has to work one-handed,
 * in one tap, at 11pm.
 *
 * The expanded equivalent is RateExpandedDialog — deliberately a different
 * shape, not this scaled up.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { posterUrl } from '@/utils/image';
import type { RateLayoutProps } from './RatePrompt';

const DISMISS_THRESHOLD = 120;
const ENTER_DURATION = 600;
const EXIT_DURATION = 400;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';

export function RateCompactSheet({
  film, loading, error, saving, showList, suggestions, behindModal,
  onChoose, onOpenSuggestion, onDismiss,
}: RateLayoutProps) {
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting'>('entering');

  useEffect(() => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => setPhase('visible'));
    });
  }, []);

  // The sheet body scrolls, not the document. Without this a swipe scrolls the
  // page behind instead of the grid, which reads as the list being stuck.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  const dismiss = useCallback(() => {
    setDragOffset(0);
    requestAnimationFrame(() => {
      setPhase('exiting');
      setTimeout(onDismiss, EXIT_DURATION);
    });
  }, [onDismiss]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-sheet-handle]') && e.touches.length > 0) {
      dragRef.current = {
        active: true,
        startY: e.touches[0]!.clientY,
        currentY: e.touches[0]!.clientY,
      };
    }
  }, []);
  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || !e.touches.length) return;
    dragRef.current.currentY = e.touches[0]!.clientY;
    setDragOffset(Math.max(0, dragRef.current.currentY - dragRef.current.startY));
  }, []);
  const onTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) dismiss();
    else setDragOffset(0);
  }, [dismiss]);

  const sheetTransform =
    dragOffset > 0 ? `translate3d(0, ${dragOffset}px, 0)`
      : phase === 'visible' ? 'translate3d(0, 0, 0)'
        : 'translate3d(0, 100%, 0)';

  const sheetTransition =
    dragOffset > 0 ? 'none'
      : phase === 'exiting' ? `transform ${EXIT_DURATION}ms cubic-bezier(0.4, 0, 1, 1)`
        : `transform ${ENTER_DURATION}ms ${SPRING}`;

  return createPortal(
    <div className="luma-bleed"
      data-sheet="RateCompactSheet"
      style={{
        position: 'fixed', inset: 0,
        zIndex: behindModal ? 9999 : 10001,
        display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
        backgroundColor: behindModal
          ? 'rgba(0,0,0,0)'
          : phase === 'visible' ? 'rgba(0,0,0,0.5)' : 'rgba(0,0,0,0)',
        transition: phase === 'exiting'
          ? `background-color ${EXIT_DURATION}ms ease`
          : `background-color ${ENTER_DURATION}ms ease`,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) dismiss(); }}
    >
      <div className="luma-bleed-panel"
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          height: showList ? 'calc(100% - env(safe-area-inset-top, 0px) - 6px)' : 'auto',
          maxHeight: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)',
          display: 'flex', flexDirection: 'column',
          background: 'var(--color-kiosk-black)',
          borderRadius: '20px 20px 0 0',
          overflow: 'hidden',
          transform: sheetTransform,
          transition: sheetTransition,
          willChange: 'transform',
          backfaceVisibility: 'hidden',
        }}
      >
        <div
          data-sheet-handle
          style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab' }}
        >
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>

        {loading ? (
          <div className="px-5 pb-10 pt-2">
            <div className="mx-auto h-[290px] w-[196px] animate-pulse rounded-2xl bg-white/8" />
            <div className="mx-auto mt-5 h-6 w-44 animate-pulse rounded bg-white/10" />
          </div>
        ) : error && !film ? (
          <div className="px-5 pb-10">
            <p className="text-[14px] text-rose-300/80">{error}</p>
            <button onClick={dismiss}
              className="mt-4 min-h-[48px] w-full rounded-xl border border-white/10 bg-white/5 text-[14px] font-medium text-white/70">
              Close
            </button>
          </div>
        ) : !showList ? (
          <div
            data-scroll-container
            className="luma-scroll-y flex flex-col items-center px-6"
            style={{
              WebkitOverflowScrolling: 'touch',
              overscrollBehavior: 'contain',
              paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 24px)',
            }}
          >
            {posterUrl(film?.poster_path, 'w500') && (
              <img
                src={posterUrl(film?.poster_path, 'w500')!}
                alt=""
                className="mt-2 w-[196px] rounded-2xl object-cover shadow-2xl ring-1 ring-white/10"
              />
            )}
            <p className="mt-6 text-[11px] text-white/35">
              You watched
            </p>
            <h1 className="mt-2 text-center text-[24px] font-semibold leading-tight text-white/95">
              {film?.title}
            </h1>
            <p className="mt-1.5 text-[13px] text-white/45">
              {[film?.year, film?.runtime ? `${film.runtime} min` : null]
                .filter(Boolean).join(' · ')}
            </p>

            <p className="mt-8 text-[15px] font-medium text-white/70">Did you like it?</p>
            <div className="mt-4 flex w-full flex-col gap-2.5">
              <button
                type="button"
                onClick={() => onChoose('liked')}
                disabled={saving !== null}
                className="min-h-[56px] w-full rounded-xl border border-emerald-400/30 bg-emerald-400/12 text-[16px] font-semibold text-emerald-100 transition-colors active:bg-emerald-400/20 disabled:opacity-50"
              >
                {saving === 'liked' ? 'Finding similar…' : 'Liked it'}
              </button>
              <button
                type="button"
                onClick={() => onChoose('disliked')}
                disabled={saving !== null}
                className="min-h-[56px] w-full rounded-xl border border-white/12 bg-white/[0.04] text-[16px] font-medium text-white/65 transition-colors active:bg-white/10 disabled:opacity-50"
              >
                {saving === 'disliked' ? 'Saving…' : 'Did not like it'}
              </button>
            </div>
            {error && <p className="mt-3 text-[12px] text-rose-300/80">{error}</p>}
          </div>
        ) : (
          <>
            <div className="shrink-0 px-5 pb-3">
              <p className="text-[11px] text-white/35">
                Because you liked
              </p>
              <h2 className="mt-1 text-[19px] font-bold leading-tight text-white">
                {film?.title}
              </h2>
              <p className="mt-0.5 text-[12px] text-white/35">
                Similar films missing from your library
              </p>
            </div>

            <div
              data-scroll-container
              className="luma-scroll-y flex-1 px-5"
              style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
            >
              {suggestions.length === 0 ? (
                <p className="text-[14px] text-white/35">
                  Nothing new to suggest — you already own the close matches.
                </p>
              ) : (
                <div className="grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-3">
                  {suggestions.map((s) => {
                    const sp = posterUrl(s.poster_path, 'w500');
                    return (
                      <button
                        key={s.tmdb_id}
                        type="button"
                        onClick={() => onOpenSuggestion(s)}
                        className="group text-left"
                      >
                        {sp ? (
                          <img
                            src={sp}
                            alt=""
                            className="aspect-[2/3] w-full rounded-xl object-cover ring-1 ring-white/10 transition-transform group-active:scale-[0.97]"
                          />
                        ) : (
                          <div className="aspect-[2/3] w-full rounded-xl bg-white/8" />
                        )}
                        {/* Reserve two lines so a wrapping title doesn't push
                            its year out of line with the rest of the row. */}
                        <p className="mt-2 line-clamp-2 min-h-[2.6em] text-[14px] font-medium leading-snug text-white/90">
                          {s.title}
                        </p>
                        <p className="text-[12px] text-white/40">{s.year || ' '}</p>
                      </button>
                    );
                  })}
                </div>
              )}
              <div style={{ height: 'calc(env(safe-area-inset-bottom, 0px) + 28px)' }} />
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
