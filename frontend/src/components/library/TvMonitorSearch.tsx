/** v184 (Taylor): a bookmark turned ON searches what it monitors that has aired and has no file. More than
 * 25 episode searches from one click ask first ('Search N episodes?') and search nothing unless confirmed. */
import { useState, type ReactNode } from 'react';
import { Modal, glassBtn, primaryBtn } from './TvEdit';
import { confirmMonitorSearch, type MonitorSearch } from './tvCalendarApi';

const plural = (n: number) => `${n} episode${n === 1 ? '' : 's'}`;

export function useMonitorSearch(say?: (msg: string) => void): [(seriesId: number, season: number | null, s?: MonitorSearch) => void, ReactNode] {
  const [ask, setAsk] = useState<{ seriesId: number; season: number | null; n: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const handle = (seriesId: number, season: number | null, s?: MonitorSearch) => {
    if (!s) return;
    if (typeof s.confirm === 'number') setAsk({ seriesId, season, n: s.confirm });
    else if (s.started) say?.(`Searching ${plural(s.started)}`);
  };
  const go = async () => {
    if (!ask || busy) return;
    setBusy(true);
    const r = await confirmMonitorSearch(ask.seriesId, ask.season);
    setBusy(false);
    setAsk(null);
    if (!r.ok) say?.(r.error); else if (r.data.started) say?.(`Searching ${plural(r.data.started)}`);
  };
  const node = ask ? (
    <Modal title={`Search ${plural(ask.n)}?`} testid="tv-monitor-search-confirm" width={420} onClose={() => setAsk(null)}
      footer={(
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12 }}>
          <button type="button" className="lp-focus tve-glass" data-testid="tv-monitor-search-cancel" onClick={() => setAsk(null)} style={glassBtn}>Cancel</button>
          <button type="button" className="lp-focus tve-primary" data-testid="tv-monitor-search-go" disabled={busy} onClick={() => void go()}
            style={primaryBtn(!busy)}>Search</button>
        </div>
      )}>
      {null}
    </Modal>
  ) : null;
  return [handle, node];
}
