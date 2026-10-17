import { useEffect, useState } from 'react';
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
    const data = (await resp.json()) as { indexer_configured?: unknown };
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
