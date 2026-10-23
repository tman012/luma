import type { NormalizedMediaItem } from '@/types/media';
import { posterUrl, backdropUrl } from '@/utils/image';
import { fetchWithTimeout } from '@/utils/fetchWithTimeout';
import { apiFetch } from '../../apiClient';

/** Map What you're missing API movies → home rail items. */
export function mapWhatYoureMissingMovies(movies: Record<string, unknown>[]): NormalizedMediaItem[] {
  return movies.map((m) => ({
    id: `missing-${m.tmdb_id ?? m.title}`,
    title: (m.title as string) ?? '',
    year: (m.year as number) ?? null,
    posterUrl: m.poster_path ? posterUrl(m.poster_path as string, 'w342')! : null,
    backdropUrl: m.backdrop_path ? backdropUrl(m.backdrop_path as string, 'w780')! : null,
    mediaType: 'movie' as const,
    tmdbId: (m.tmdb_id as number) ?? null,
    imdbId: (m.imdb_id as string) ?? null,
    ratingKey: null,
    owned: false,
    inWatchlist: false,
    voteAverage: (m.imdb_rating as number) ?? (m.rating as number) ?? null,
  }));
}

export async function fetchWhatYoureMissingItems(): Promise<NormalizedMediaItem[]> {
  const resp = await fetchWithTimeout('/api/rails/what-youre-missing?limit=20');
  if (!resp.ok) return [];
  const data = await resp.json();
  if (data.empty) return [];
  return mapWhatYoureMissingMovies((data.movies ?? []) as Record<string, unknown>[]);
}

export async function dismissWhatYoureMissing(
  tmdbId: number,
): Promise<{ ok: boolean; items: NormalizedMediaItem[]; error?: string }> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 10_000);
  try {
    const resp = await apiFetch('/api/rails/what-youre-missing/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ tmdb_id: tmdbId }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      return { ok: false, items: [], error: (err as { error?: string }).error || `HTTP ${resp.status}` };
    }
    const data = await resp.json();
    const movies = ((data.rail?.movies ?? []) as Record<string, unknown>[]);
    return { ok: true, items: mapWhatYoureMissingMovies(movies) };
  } finally {
    window.clearTimeout(timer);
  }
}
