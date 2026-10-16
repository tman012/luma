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
  // Already a proxy URL — use as-is
  if (path.startsWith('/api/') || path.startsWith('http')) return path;
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
  return resolveImageUrl(path, size);
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
  if (!url.startsWith('/api/img/plex')) return url;
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
