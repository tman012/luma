import { useEffect, useState, useSyncExternalStore } from 'react';
import { apiFetch } from '../apiClient';

/**
 * Whether Sources has anything to search: Luma's own trackers (Settings >
 * Trackers) or, on installs that still have one, an indexer
 * connection. Read from /api/sources/config each time a Sources sheet opens,
 * so a tracker added a moment ago counts.
 *
 * Resolves false only when the server says so; any failure counts as true,
 * so the live app's empty state is kept whenever we can't tell.
 */
export async function fetchIndexerConfigured(signal?: AbortSignal): Promise<boolean> {
  try {
    const resp = await apiFetch('/api/sources/config', { signal });
    if (!resp.ok) return true;
    const data = (await resp.json()) as { indexer_configured?: unknown; download_client_configured?: unknown };
    setDownloadClientReady(data.download_client_configured !== false);
    return data.indexer_configured !== false;
  } catch {
    return true;
  }
}

/** null while loading, then whether there is anything to search. */
export function useIndexerConfigured(): boolean | null {
  const [configured, setConfigured] = useState<boolean | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    fetchIndexerConfigured(controller.signal).then((v) => {
      if (live) setConfigured(v);
    });
    return () => {
      live = false;
      controller.abort();
    };
  }, []);
  return configured;
}

// Whether a download client (qBittorrent) is set up, from the same config
// answer. Unknown counts as ready, so the server's own answer still decides.
let downloadClientReady = true;
const subscribers = new Set<() => void>();

export function setDownloadClientReady(ready: boolean): void {
  if (ready === downloadClientReady) return;
  downloadClientReady = ready;
  subscribers.forEach((fn) => fn());
}

export function isDownloadClientReady(): boolean {
  return downloadClientReady;
}

/** Re-renders when the download client flag changes. */
export function useDownloadClientReady(): boolean {
  return useSyncExternalStore(
    (fn) => { subscribers.add(fn); return () => { subscribers.delete(fn); }; },
    () => downloadClientReady,
  );
}
