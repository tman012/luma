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

/** One line above the results when some trackers answered and others did not,
 * e.g. "BHD unavailable" while IPTorrents rows are shown, with Retry. */
export function TrackerStatusBar({ trackers, onRetry, fontSize = 15 }: {
  trackers: TrackerStatus[];
  onRetry?: () => void;
  fontSize?: number;
}) {
  if (trackers.length === 0) return null;
  return (
    <div
      data-sources-state="trackers-partial"
      style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center', gap: '8px 16px', textAlign: 'center', margin: '0 0 12px', ...srcAbove }}
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
          style={{ ...srcCancelBtn, padding: '6px 16px', borderRadius: '10px', fontSize: `${Math.min(fontSize, 15)}px`, fontWeight: 600, cursor: 'pointer' }}
        >
          Retry
        </button>
      )}
    </div>
  );
}
