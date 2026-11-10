/**
 * ActivitySheet — bottom sheet showing the Activity feed (last 30 downloads).
 * Mirrors the DownloadsSheet/LatestSheet shell: slide-up, grab-handle
 * drag-to-dismiss, tap-outside to close. The shell is the shared SimpleSheet (shared motion tokens).
 */
import { useState, useEffect } from 'react';
import { SimpleSheet } from '@/components/layout/SimpleSheet';
import { MOTION } from '@/theme/motion';
import { ActivityFeed } from './ActivityFeed';

interface ActivitySheetProps {
  open: boolean;
  onClose: () => void;
}

export function ActivitySheet({ open, onClose }: ActivitySheetProps) {
  if (!open) return null;
  return <ActivitySheetBody onClose={onClose} />;
}

function ActivitySheetBody({ onClose }: { onClose: () => void }) {
  const [behindModal, setBehindModal] = useState(false);

  // When a detail modal opens from a feed item, drop behind it; restore on close.
  useEffect(() => {
    if (!behindModal) return;
    const restore = () => setBehindModal(false);
    window.addEventListener('modal-closed', restore);
    return () => window.removeEventListener('modal-closed', restore);
  }, [behindModal]);

  return (
    <SimpleSheet name="ActivitySheet" onClosed={onClose} behind={behindModal}
      rootClassName="luma-fullscreen luma-bleed" panelClassName="luma-bleed-panel"
      panelStyle={{
        height: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)',
        display: 'flex', flexDirection: 'column',
        background: 'var(--color-kiosk-black)',
        borderRadius: '20px 20px 0 0',
        overflow: 'hidden',
      }}>
      {/* Grab handle */}
      <div data-sheet-handle
        style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab' }}
      >
        <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
      </div>

      {/* Title */}
      <div className="shrink-0 px-4 pb-3">
        <h2 className="text-[18px] font-bold text-white">Activity</h2>
      </div>

      {/* Scrollable content */}
      <div
        data-scroll-container
        className="luma-scroll-y flex-1 px-3 pb-2"
        style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
      >
        <ActivityFeed embedded onOpen={() => setBehindModal(true)} reveal={{ id: 'activity-sheet', landMs: MOTION.dur.sheetEnter }} />
        <div className="shrink-0" style={{ height: 'env(safe-area-inset-bottom, 12px)' }} />
      </div>
    </SimpleSheet>
  );
}
