/**
 * TvCalendarSheet: the phone home screen's TV calendar, on Luma's own TV data (/api/tv/calendar, the same
 * as Settings > TV > Calendar; Sonarr is not read). v204: Taylor's phone picks: day-nav A (one day at a
 * time with ‹ ›), the Q2a quality chip, T2 air-time headers in ET, and a tap opens that show's series page.
 *
 * The slide-up shell (grab-handle drag-to-dismiss, tap-outside to close) is unchanged.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { SimpleSheet } from '@/components/layout/SimpleSheet';
import { MOTION } from '@/theme/motion';
import { OpenShowContext, TvCalendar } from '@/components/library/TvCalendar';
import { TvSeriesDetail } from '@/components/library/TvSeriesDetail';
import { NoticeToast } from '@/components/start/SetupToast';
import { localDay, weekStart, type Day } from '@/components/library/tvCalendarApi';


interface TvCalendarSheetProps {
  open: boolean;
  onClose: () => void;
}

export function TvCalendarSheet({ open, onClose }: TvCalendarSheetProps) {
  if (!open) return null;
  return <TvCalendarSheetBody onClose={onClose} />;
}

function TvCalendarSheetBody({ onClose }: { onClose: () => void }) {
  // The shell (enter, exit, scrim, drag) is the shared SimpleSheet.
  // --- Day and week (Luma's TV calendar; the day is picked inside TvCalendar's day-nav A) ---
  const [today] = useState<Day>(() => localDay());
  const [week, setWeek] = useState<Day>(() => weekStart(localDay()));
  const [seriesId, setSeriesId] = useState<number | null>(null);
  const [toast, setToast] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const savedScroll = useRef(0);
  // a tap opens the series page inside the sheet; the calendar stays mounted so the day survives Back
  const openSeries = useCallback((id: number) => {
    savedScroll.current = scrollRef.current?.scrollTop ?? 0;
    setSeriesId(id);
  }, []);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (seriesId != null) { el.scrollTop = 0; return; }
    const y = savedScroll.current;
    el.scrollTop = y;
    const id = requestAnimationFrame(() => { if (scrollRef.current) scrollRef.current.scrollTop = y; });
    return () => cancelAnimationFrame(id);
  }, [seriesId]);

  return (
    <SimpleSheet name="TvCalendarSheet" onClosed={onClose}
      rootClassName="luma-fullscreen"
      // v266: size by --luma-screen-h like Home (negative-bottom / ::after stay clipped on iPhone PWA)
      rootStyle={{ inset: 'auto', top: 0, left: 0, right: 0 }}
      panelStyle={{
        height: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)',
        display: 'flex', flexDirection: 'column',
        background: '#111318',                    // v322 option C (TOKENS-C): page and sheet #111318
        borderRadius: '20px 20px 0 0',
        overflow: 'hidden',
      }}>
        {/* Grab handle */}
        <div data-sheet-handle
          style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '10px 0 14px', cursor: 'grab' }}
        >
          <div style={{ width: 36, height: 4, borderRadius: 2, background: '#45474e' }} />
        </div>

        <div
          ref={scrollRef}
          data-scroll-container
          className="luma-scroll-y flex-1 px-4"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y', overflowX: 'hidden', overflowY: 'auto', minWidth: 0, maxWidth: '100%' }}
        >
          {toast && <NoticeToast key={toast} text={toast} onDone={() => setToast('')} />}
          <div style={{ display: seriesId ? 'none' : undefined }} hidden={seriesId != null}>
            <OpenShowContext.Provider value={openSeries}>
              <TvCalendar today={today} week={week} onWeek={setWeek} toolbar={false} phone reveal={{ id: 'tv-calendar-sheet', landMs: MOTION.dur.sheetEnter }} />
            </OpenShowContext.Provider>
          </div>
          {seriesId != null && (
            <div data-testid="tv-cal-series" style={{ minWidth: 0, width: '100%', maxWidth: '100%', overflowX: 'hidden' }}>
              <TvSeriesDetail key={seriesId} seriesId={seriesId} episodeId={null} wide={false} phone
                onBack={() => setSeriesId(null)} say={setToast} />
            </div>
          )}
          <div data-scroll-content className="shrink-0" style={{ height: 'calc(var(--luma-end-pad) + 12px)' }} />
        </div>
    </SimpleSheet>
  );
}
