/**
 * Recently added TV — one card per show.
 * Dedupe by show_rating_key (Plex show key > 0) or show_title; skip rows with neither.
 */
import type { NormalizedMediaItem } from '@/types/media';

export function dedupeRecentTvRows(
  episodes: Record<string, unknown>[],
): NormalizedMediaItem[] {
  const seen = new Set<string>();
  const out: NormalizedMediaItem[] = [];
  for (const ep of episodes) {
    const rkRaw = ep.show_rating_key;
    const rk = typeof rkRaw === 'number' ? rkRaw : null;
    const title = String(ep.show_title ?? '').trim();
    let identity: string | null = null;
    if (rk != null && rk > 0) identity = `rk:${rk}`;
    else if (title) identity = `title:${title.toLowerCase()}`;
    if (!identity || seen.has(identity)) continue;
    seen.add(identity);
    const plexRk = rk != null && rk > 0 ? rk : null;
    out.push({
      id: `recent-tv-${identity}`,
      title,
      year: (ep.show_year as number) ?? null,
      posterUrl: (ep.poster_url as string) ?? null,
      mediaType: 'tv' as const,
      tmdbId: (ep.tmdb_id as number) ?? null,
      ratingKey: plexRk,
      showRatingKey: plexRk,
      owned: true,
      inWatchlist: false,
    });
  }
  return out;
}
