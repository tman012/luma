/** v183 (Taylor): the Sonarr-style monitored bookmark, in place of the monitor switches on the series detail page
 * (show, seasons, episodes) and in the Series list's Monitored column. Filled = monitored, outline = not; a click
 * flips it. The same icon and dark rounded square as the calendar card's bookmark. tv.db only: it never searches. */
import { FONT } from '@/components/start/StartUI';

export const FLAG_SIZE = 28;          // the hit area (>= 24px)
export const flagLabel = (on: boolean) => (on ? 'Monitored' : 'Not monitored');

export const monitorFlagCss = `
.tv-flag{transition:background 120ms,border-color 120ms}
.tv-flag:hover:not(:disabled){background:rgba(59,130,246,.35)!important}
`;

export function MonitorFlag({ on, onChange, testid, busy = false, title }: {
  on: boolean; onChange: (on: boolean) => void; testid: string; busy?: boolean;
  /** the row it belongs to (a tooltip; the accessible name stays Monitored / Not monitored) */
  title?: string;
}) {
  return (
    <button type="button" className="lp-focus tv-flag" data-testid={testid} aria-pressed={on} aria-label={flagLabel(on)}
      title={title} disabled={busy} data-on={on || undefined}
      onClick={e => { e.stopPropagation(); onChange(!on); }}
      style={{ width: FLAG_SIZE, height: FLAG_SIZE, padding: 0, flex: 'none', borderRadius: 7, display: 'inline-flex', alignItems: 'center',
        justifyContent: 'center', background: 'rgba(21,16,42,.72)', border: `1px solid ${on ? 'rgba(255,255,255,0.16)' : 'rgba(255,255,255,0.10)'}`,
        color: on ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.6)', cursor: busy ? 'default' : 'pointer', fontFamily: FONT,
        opacity: busy ? 0.6 : 1 }}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill={on ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2"
        strokeLinejoin="round" aria-hidden="true">
        <path d="M6 3h12v18l-6-4.5L6 21z" />
      </svg>
    </button>
  );
}
