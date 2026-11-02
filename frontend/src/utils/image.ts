import { getUrlBase, withBase } from '@/urlHelpers';
import { isMinimal } from '@/minimal';

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
  if (path.startsWith('http')) return tmdbImg(path);
  // TMDB path — prepend base (through our proxy in minimal mode)
  if (isMinimal() && path.startsWith('/')) return tmdbProxy(path, size);
  return `${TMDB_IMAGE_BASE}/${size}${path}`;
}

const TMDB_URL = /^https?:\/\/image\.tmdb\.org\/t\/p\/(w\d+|h\d+|original)(\/[^?#]+)$/;

function tmdbProxy(path: string, size: string): string {
  return withBase(`/api/img/tmdb?path=${encodeURIComponent(path)}&size=${encodeURIComponent(size)}`);
}

/**
 * TMDb CDN images load through our /api/img/tmdb proxy in minimal mode, the
 * way prod's Latest and Nova sheets do, so a browser or network that can't
 * reach image.tmdb.org still gets posters. Anything else is returned as-is.
 */
export function tmdbImg(url: string | undefined | null): string | null {
  if (!url) return null;
  if (!isMinimal()) return url;
  const m = TMDB_URL.exec(url);
  return m ? tmdbProxy(m[2]!, m[1]!) : url;
}

const TVDB_ART = /^https:\/\/artworks\.thetvdb\.com(\/[A-Za-z0-9/_.-]+)$/;

/** A TheTVDB artwork URL through Luma's image proxy (never loaded direct); anything else is null. */
export function tvdbImg(url: string | undefined | null): string | null {
  const m = url ? TVDB_ART.exec(url) : null;
  if (!m || m[1]!.includes('..') || m[1]!.includes('//')) return null;
  return withBase(`/api/img/tmdb?host=tvdb&path=${encodeURIComponent(m[1]!)}`);
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
  // Phones (minimal): w780 instead of the multi-MB original, so the popup's
  // backdrop decodes fast enough to reveal with everything else.
  return resolveImageUrl(path, phoneMinimal() && size === 'original' ? 'w780' : size);
}

/** The sharp version of a popup backdrop that phones first load small (w780 / a 780px Plex transcode):
 *  TMDb w1280 (a 390pt phone at 3x is ~1170px wide), Plex 1280x720. Null when the URL is already the sharp one.
 *  The sheet shows the small or seeded art first and crossfades to this once it has decoded. */
export function backdropHiRes(url: string | null | undefined): string | null {
  if (!url) return null;
  let up = url
    .replace(/([?&]size=)w780(?=&|$)/, '$1w1280')
    .replace(/\/t\/p\/w780\//, '/t/p/w1280/');
  if (/\/api\/img\/plex/.test(up)) up = up.replace(/([?&])w=780(&h=439)?(?=&|$)/, (_m, sep) => `${sep}w=1280&h=720`);
  return up === url ? null : up;
}

/** The one backdrop a movie/TV sheet shows: the sharp size on phones (backdropHiRes), the URL as is elsewhere. */
export function finalBackdropUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return phoneMinimal() ? backdropHiRes(url) ?? url : url;
}

/** Minimal mode on a phone-width screen (the desktop popup keeps its sizes). */
export function phoneMinimal(): boolean {
  return isMinimal() && typeof window !== 'undefined' && window.innerWidth < 1024;
}

/** Plex art for the popup backdrop: a 780x439 transcode on phones (minimal). */
export function plexBackdrop(url: string | null | undefined): string | null {
  if (!url) return null;
  // One URL per art: an encoded path (Home's cards) and the raw one (the details) load the same image once.
  url = url.replace(/([?&]url=)(%2F[^&]*)/i, (_m, k: string, v: string) => k + decodeURIComponent(v));
  return phoneMinimal() ? sizedPlexImg(url, 780, 439) : url;
}

/** The Plex art as the sheet shows it (finalBackdropUrl of plexBackdrop): a tap or a warm-up preloads this exact URL. */
export function plexSheetArt(url: string | null | undefined): string | null {
  return finalBackdropUrl(plexBackdrop(url));
}

/** A clearlogo URL as the popup loads it: w300 (TMDb or Plex) on phones
 *  (minimal), through the TMDb proxy in minimal mode. */
export function logoImg(url: string | null | undefined): string | null {
  if (!url) return null;
  if (!phoneMinimal()) return tmdbImg(url);
  // Phones: TMDb w300, or a 300px-wide Plex transcode (a logo is ~240 CSS px).
  return sizedPlexImg(tmdbImg(url.replace('/t/p/original/', '/t/p/w300/')), 300);
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

/** Plex's own people photos (metadata-static.plex.tv) are multi-MB originals:
 *  load them as a small transcode through the image proxy instead. */
const PLEX_PEOPLE = /^https:\/\/metadata-static\.plex\.tv\/[0-9a-f]\/people\/[0-9a-f]{32}\.(?:jpe?g|png)$/i;
export const CAST_THUMB_W = 120;

export function castThumb(url: string | null | undefined): string | null {
  if (!url) return null;
  if (PLEX_PEOPLE.test(url)) return withBase(`/api/img/plex?url=${encodeURIComponent(url)}&w=${CAST_THUMB_W}`);
  return tmdbImg(url.replace('/t/p/original/', '/t/p/w185/'));
}
