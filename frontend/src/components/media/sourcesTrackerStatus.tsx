/** Per-tracker status in place of the empty state, e.g. "IPTorrents sign-in
 * failed", with Retry (a fresh search that skips the server's caches). */
import { trackerStatusText, type TrackerStatus } from '@/services/allSourcesApi';
import { srcAbove, srcCancelBtn, SRC_HEADING_COLOR, SRC_WARN } from './sourcesTheme';

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

/** One line inside the content column, 12px above the first group, when some
 * trackers answered and others did not (e.g. "BHD unavailable" over the
 * IPTorrents rows). Not a banner or a toast: 14px/500 white 70% text with an
 * amber dot, and a glass Retry pill on the same line, right-aligned (44px tall
 * on phone, 32px on desktop). While Retry runs the rows stay; on success the
 * line goes away and nothing else moves. */
export function TrackerStatusBar({ trackers, onRetry, retrying = false, phone = false }: {
  trackers: TrackerStatus[];
  onRetry?: () => void;
  retrying?: boolean;
  phone?: boolean;
}) {
  if (trackers.length === 0) return null;
  const h = phone ? 44 : 32;
  return (
    <div
      data-sources-state="trackers-partial"
      style={{
        display: 'flex', alignItems: 'center', gap: '12px', width: '100%', maxWidth: '560px',
        boxSizing: 'border-box', margin: '0 auto 12px', minHeight: `${h}px`, ...srcAbove,
      }}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '4px 12px', flex: '1 1 auto', minWidth: 0 }}>
        {trackers.map((t) => (
          <span
            key={t.name}
            data-tracker-status={t.status}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', fontSize: '14px', fontWeight: 500, lineHeight: '20px', color: SRC_HEADING_COLOR }}
          >
            <span aria-hidden="true" data-status-dot="" style={{ width: '8px', height: '8px', borderRadius: '50%', background: SRC_WARN, flex: '0 0 auto' }} />
            {trackerStatusText(t)}
          </span>
        ))}
      </div>
      {onRetry && (
        <button
          type="button"
          data-sources-retry=""
          onClick={onRetry}
          disabled={retrying}
          aria-busy={retrying || undefined}
          style={{
            ...srcCancelBtn, flex: '0 0 auto', height: `${h}px`, padding: phone ? '0 20px' : '0 16px',
            borderRadius: '999px', fontSize: '14px', fontWeight: 600,
            cursor: retrying ? 'default' : 'pointer', opacity: retrying ? 0.6 : 1,
          }}
        >
          Retry
        </button>
      )}
    </div>
  );
}
