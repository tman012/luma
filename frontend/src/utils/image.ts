import { getUrlBase, withBase } from '@/urlHelpers';

const TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p';

type PosterSize = 'w185' | 'w342' | 'w500' | 'w780' | 'original';
type BackdropSize = 'w780' | 'w1280' | 'original';

/**
 * Returns a usable image URL. If the path is already a proxy URL
 * (starts with /api/), returns it as-is. If it's a TMDB path,
 * prepends the TMDB image base.
 */
function resolveImageUrl(
  path: string | undefined | null,
  size: string,
): string | null {
  if (!path) return null;
  // Already a proxy URL — keep it, under the URL base
  if (path.startsWith('/api/')) return withBase(path);
  if (path.startsWith('http')) return path;
  // TMDB path — prepend base
  return `${TMDB_IMAGE_BASE}/${size}${path}`;
}

export function posterUrl(
  path: string | undefined | null,
  size: PosterSize = 'w500',
): string | null {
  return resolveImageUrl(path, size);
}

export function backdropUrl(
  path: string | undefined | null,
  size: BackdropSize = 'original',
): string | null {
  // Phones: w780 instead of the multi-MB original, so the popup's backdrop
  // decodes fast enough to reveal with everything else.
  return resolveImageUrl(path, phoneScreen() && size === 'original' ? 'w780' : size);
}

/** Phone-width screen: the detail popup's compact layout (desktop keeps its sizes). */
export function phoneScreen(): boolean {
  return typeof window !== 'undefined' && window.innerWidth < 768;
}

/** Plex art for the popup backdrop: a 780x439 transcode on phones. */
export function plexBackdrop(url: string | null | undefined): string | null {
  if (!url) return null;
  return phoneScreen() ? sizedPlexImg(url, 780, 439) : url;
}

/** A clearlogo URL as the popup loads it: on phones, TMDb w500 or a
 *  500px-wide Plex transcode. */
export function logoImg(url: string | null | undefined): string | null {
  if (!url) return null;
  if (!phoneScreen()) return url;
  return sizedPlexImg(url.replace('/t/p/original/', '/t/p/w500/'), 500);
}

/**
 * Requests a resized copy of a Plex-proxied image (/api/img/plex?url=...).
 * Plex serves ~1MB full-res originals by default; for thumbnails and grid
 * cards this appends w/h so the backend transcodes to a much smaller copy.
 * Non-Plex-proxy URLs (TMDb CDN, etc.) are returned unchanged.
 */
export function sizedPlexImg(
  url: string | undefined | null,
  w: number,
  h?: number,
): string | null {
  if (!url) return null;
  const base = getUrlBase();
  const local = base && url.startsWith(`${base}/`) ? url.slice(base.length) : url;
  if (!local.startsWith('/api/img/plex')) return url;
  url = withBase(local);
  if (/[?&]w=/.test(url)) return url; // already sized
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}w=${w}${h ? `&h=${h}` : ''}`;
}

export function formatRuntime(ms: number | undefined | null): string {
  if (!ms) return '';
  const minutes = Math.floor(ms / 60_000);
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
}

export function formatSize(bytes: number): string {
  const gb = bytes / (1024 * 1024 * 1024);
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(gb * 1024).toFixed(0)} MB`;
}
