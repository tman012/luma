/**
 * Pick the best YouTube trailer key from a TMDb videos result set.
 *
 * Preference order:
 *   1. Official Trailer (type=Trailer, official=true, name contains "Official Trailer")
 *   2. Any official Trailer
 *   3. Any Trailer
 *   4. Teaser
 *   5. Any other YouTube video
 *
 * Within each tier, prefer higher `size` (resolution).
 */

interface TmdbVideo {
  key: string;
  site: string;
  type: string;
  name: string;
  official?: boolean;
  size?: number;
}

export function pickBestTrailer(
  videos: TmdbVideo[] | undefined | null,
): string | null {
  if (!videos || videos.length === 0) return null;

  // Only YouTube
  const yt = videos.filter((v) => v.site === 'YouTube' && v.key);
  if (yt.length === 0) return null;

  // Score each video — higher is better
  function score(v: TmdbVideo): number {
    let s = 0;
    const nameLower = (v.name ?? '').toLowerCase();

    if (v.type === 'Trailer') s += 1000;
    else if (v.type === 'Teaser') s += 500;

    if (v.official) s += 200;
    if (nameLower.includes('official trailer')) s += 100;
    if (nameLower.includes('trailer')) s += 50;

    // Prefer higher resolution
    s += (v.size ?? 0) / 10;

    return s;
  }

  yt.sort((a, b) => score(b) - score(a));
  return yt[0]!.key;
}
