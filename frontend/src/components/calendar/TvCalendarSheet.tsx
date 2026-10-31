/**
 * TvCalendarSheet: the phone home screen's TV calendar, on Luma's own TV data (/api/tv/calendar, the same
 * as Settings > TV > Calendar; Sonarr is not read). v204: Taylor's phone picks: day-nav A (one day at a
 * time with ‹ ›), the Q2a quality chip, T2 air-time headers in ET, and a tap opens the E2 episode page.
 *
 * The slide-up shell (grab-handle drag-to-dismiss, tap-outside to close) is unchanged.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { OpenShowContext, TvCalendar } from '@/components/library/TvCalendar';
import { TvEpisodePage } from '@/components/library/TvEpisodePage';
import { localDay, weekStart, type Day } from '@/components/library/tvCalendarApi';

const DISMISS_THRESHOLD = 120;
const ENTER_DURATION = 600;
const EXIT_DURATION = 400;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';


interface TvCalendarSheetProps {
  open: boolean;
  onClose: () => void;
}

export function TvCalendarSheet({ open, onClose }: TvCalendarSheetProps) {

  // --- Sheet animation (shared shell) ---
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting' | 'gone'>('gone');
  const [behindModal, setBehindModal] = useState(false);

  useEffect(() => {
    if (open) {
      setPhase('entering');
      setDragOffset(0);
      setBehindModal(false);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setPhase('visible'));
      });
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prevOverflow; };
  }, [open]);

  const handleDismiss = useCallback(() => {
    setDragOffset(0);
    requestAnimationFrame(() => {
      setPhase('exiting');
      setTimeout(() => { setPhase('gone'); onClose(); }, EXIT_DURATION);
    });
  }, [onClose]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-sheet-handle]') && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
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
    if (dy > DISMISS_THRESHOLD) handleDismiss();
    else setDragOffset(0);
  }, [handleDismiss]);

  useEffect(() => {
    if (!behindModal) return;
    const restore = () => setBehindModal(false);
    window.addEventListener('modal-closed', restore);
    return () => window.removeEventListener('modal-closed', restore);
  }, [behindModal]);

  // --- Day and week (Luma's TV calendar; the day is picked inside TvCalendar's day-nav A) ---
  const [today, setToday] = useState<Day>(() => localDay());
  const [week, setWeek] = useState<Day>(() => weekStart(localDay()));
  const [ep, setEp] = useState<{ seriesId: number; episodeId: number } | null>(null);
  useEffect(() => {
    if (!open) return;
    const t = localDay();
    setToday(t); setWeek(weekStart(t)); setEp(null);
  }, [open]);
  // a tap opens the E2 episode page inside the sheet; back returns to the same day
  const openEpisode = useCallback((seriesId: number, episodeId?: number | null) => {
    if (episodeId) setEp({ seriesId, episodeId });
  }, []);

  if (phase === 'gone' && !open) return null;

  const sheetTransform =
    dragOffset > 0 ? `translate3d(0, ${dragOffset}px, 0)`
      : phase === 'visible' ? 'translate3d(0, 0, 0)'
        : 'translate3d(0, 100%, 0)';

  const sheetTransition =
    dragOffset > 0 ? 'none'
      : phase === 'exiting' ? `transform ${EXIT_DURATION}ms cubic-bezier(0.4, 0, 1, 1)`
        : `transform ${ENTER_DURATION}ms ${SPRING}`;

  return createPortal(
    <div
      data-sheet="TvCalendarSheet"
      className="luma-fullscreen"
      style={{
        // v266: size by --luma-screen-h like Home (negative-bottom / ::after stay clipped on iPhone PWA)
        position: 'fixed', top: 0, left: 0, right: 0,
        zIndex: behindModal ? 9999 : 10001,
        display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
        backgroundColor: behindModal ? 'rgba(0,0,0,0)' : phase === 'visible' ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0)',
        transition: phase === 'exiting'
          ? `background-color ${EXIT_DURATION}ms ease`
          : `background-color ${ENTER_DURATION}ms ease`,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
    >
      <div
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          height: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)',
          display: 'flex', flexDirection: 'column',
          background: '#111318',                    // v322 option C (TOKENS-C): page and sheet #111318
          borderRadius: '20px 20px 0 0',
          overflow: 'hidden',
          transform: sheetTransform,
          transition: sheetTransition,
          willChange: 'transform',
          backfaceVisibility: 'hidden',
        }}
      >
        {/* Grab handle */}
        <div data-sheet-handle
          style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '10px 0 14px', cursor: 'grab' }}
        >
          <div style={{ width: 36, height: 4, borderRadius: 2, background: '#45474e' }} />
        </div>

        <div
          data-scroll-container
          className="luma-scroll-y flex-1 px-4"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
        >
          {ep ? (
            <TvEpisodePage key={`${ep.seriesId}/${ep.episodeId}`} seriesId={ep.seriesId} episodeId={ep.episodeId}
              onBack={() => setEp(null)} />
          ) : (
            <OpenShowContext.Provider value={openEpisode}>
              <TvCalendar today={today} week={week} onWeek={setWeek} toolbar={false} phone reveal={{ id: 'tv-calendar-sheet', landMs: ENTER_DURATION }} />
            </OpenShowContext.Provider>
          )}
          <div data-scroll-content className="shrink-0" style={{ height: 'calc(var(--luma-end-pad) + 12px)' }} />
        </div>
      </div>
    </div>,
    document.body,
  );
}
