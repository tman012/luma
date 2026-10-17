/** Per-tracker status in place of the empty state, e.g. "IPTorrents sign-in
 * failed", with Retry (a fresh search that skips the server's caches). */
import { trackerStatusText, type TrackerStatus } from '@/services/allSourcesApi';
import { srcAbove, srcCancelBtn, SRC_HEADING_COLOR } from './sourcesTheme';

export function TrackerStatusNotice({ trackers, onRetry, fontSize = 17 }: {
  trackers: TrackerStatus[];
  onRetry?: () => void;
  fontSize?: number;
}) {
  return (
    <div
      data-sources-state="trackers"
      style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', textAlign: 'center', ...srcAbove }}
    >
      {trackers.map((t) => (
        <span key={t.name} data-tracker-status={t.status} style={{ fontSize: `${fontSize}px`, fontWeight: 600, color: SRC_HEADING_COLOR }}>
          {trackerStatusText(t)}
        </span>
      ))}
      {onRetry && (
        <button
          type="button"
          data-sources-retry=""
          onClick={onRetry}
          style={{ ...srcCancelBtn, marginTop: '12px', padding: '10px 24px', borderRadius: '12px', fontSize: '15px', fontWeight: 600, cursor: 'pointer' }}
        >
          Retry
        </button>
      )}
    </div>
  );
}

/** Partial failure (some trackers answered, some did not): one muted line under
 * the step heading, so the heading stays put. */
export function TrackerStatusLine({ trackers }: { trackers: TrackerStatus[] }) {
  if (trackers.length === 0) return null;
  return (
    <p
      data-sources-state="trackers-partial"
      style={{ fontSize: '13px', fontWeight: 500, color: 'rgba(255,255,255,0.45)', textAlign: 'center', margin: '-12px 0 20px' }}
    >
      {trackers.map(trackerStatusText).join(' · ')}
    </p>
  );
}
