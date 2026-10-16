/** Helpers for the request-page settings card (link and recent requests). */
import { apiUrl } from '../../urlHelpers';

export interface RequestLogEntry {
  id: string;
  ts: number;
  who: string;
  kind: 'movie' | 'tv';
  title: string;
  scope: string;
  season?: number | null;
  episodes?: number[] | null;
  action: string;
}

export const RECENT_REQUESTS = 12;

/** The link to share: the server's public URL, else this server's /request page. */
export function requestLink(publicUrl: string | undefined, origin: string): string {
  return publicUrl || `${origin}${apiUrl('/request')}`;
}

export function describeRequest(e: RequestLogEntry): string {
  if (e.kind === 'movie') return e.title;
  if (e.scope === 'show') return `${e.title} · All Seasons`;
  if (e.scope === 'season') return `${e.title} · Season ${e.season}`;
  const eps = (e.episodes || []).map((n) => `E${n}`).join(', ');
  return `${e.title} · S${e.season} ${eps}`;
}

export function requestTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export function recentRequests(raw: unknown): RequestLogEntry[] {
  return Array.isArray(raw) ? (raw as RequestLogEntry[]).slice(0, RECENT_REQUESTS) : [];
}
