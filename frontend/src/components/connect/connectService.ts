/**
 * "Connect <service>" prompts for optional services that aren't set up yet.
 *
 * - installConnectServiceFetchHook(): any API error carrying `needs_setup`
 *   opens the sheet for that service (once per session per service).
 * - useRequireService(service): pages that only work with a service (TMDB
 *   for Discover, Radarr for requests, …) open the sheet the first time.
 */
import { useEffect, useState } from 'react';

export type ServiceId = 'tmdb' | 'radarr' | 'sonarr' | 'qbittorrent';
export const CONNECT_EVENT = 'luma:connect-service';

const SHOWN_KEY = 'luma.connectShown';

function shownSet(): Set<string> {
  try { return new Set(JSON.parse(sessionStorage.getItem(SHOWN_KEY) || '[]')); } catch { return new Set(); }
}

export function requestConnect(service: ServiceId, opts: { force?: boolean } = {}) {
  const shown = shownSet();
  if (!opts.force && shown.has(service)) return;
  shown.add(service);
  try { sessionStorage.setItem(SHOWN_KEY, JSON.stringify([...shown])); } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent(CONNECT_EVENT, { detail: { service } }));
}

const KNOWN = new Set<ServiceId>(['tmdb', 'radarr', 'sonarr', 'qbittorrent']);

export function installConnectServiceFetchHook() {
  const w = window as unknown as { __lumaConnectHook?: boolean };
  if (w.__lumaConnectHook) return;
  w.__lumaConnectHook = true;
  const original = window.fetch.bind(window);
  window.fetch = async (...args: Parameters<typeof fetch>) => {
    const res = await original(...args);
    if (!res.ok && (res.headers.get('content-type') || '').includes('application/json')) {
      res.clone().json().then((data) => {
        const svc = data && typeof data === 'object' ? (data as { needs_setup?: unknown }).needs_setup : null;
        if (typeof svc === 'string' && KNOWN.has(svc as ServiceId)) requestConnect(svc as ServiceId);
      }).catch(() => {});
    }
    return res;
  };
}

type Status = Partial<Record<ServiceId | 'plex', boolean>>;
let statusPromise: Promise<Status> | null = null;

export function integrationsStatus(refresh = false): Promise<Status> {
  if (!statusPromise || refresh) {
    statusPromise = fetch('/api/integrations/status')
      .then(r => (r.ok ? r.json() : {}))
      .catch(() => ({}));
  }
  return statusPromise;
}

/** Returns connected (true/false) or null while loading; prompts once if not connected. */
export function useRequireService(service: ServiceId, prompt = true): boolean | null {
  const [connected, setConnected] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    integrationsStatus().then((s) => {
      if (!alive) return;
      const ok = !!s[service];
      setConnected(ok);
      if (!ok && prompt) requestConnect(service);
    });
    const onSaved = () => integrationsStatus(true).then(s => alive && setConnected(!!s[service]));
    window.addEventListener('luma:service-connected', onSaved);
    return () => { alive = false; window.removeEventListener('luma:service-connected', onSaved); };
  }, [service, prompt]);
  return connected;
}
