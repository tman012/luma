/** Natural language search API service */

import type { RawVibeItem } from '@/types/vibe';
import { apiFetch } from '../apiClient';

interface ParsedSearchParams {
  genres: string[];
  moods: string[];
  media_type: 'movie' | 'tv' | 'any';
}

export interface UnownedItem {
  title: string;
  year: number | null;
  type: 'movie' | 'tv';
  tmdb_id: number;
  rating: number | null;
  vote_count: number;
  overview: string;
  poster_url: string | null;
  backdrop_url: string | null;
  genres: string[];
  runtime_minutes: number;
  content_rating: string;
  ai_reason?: string;
  ai_category?: string;
  ai_rank?: number;
}

interface NaturalSearchResponse {
  owned: RawVibeItem[];
  unowned: UnownedItem[];
  parsed: ParsedSearchParams;
  description: string;
}

export async function performNaturalSearch(
  description: string,
  mediaType?: 'movie' | 'tv' | 'any',
  signal?: AbortSignal,
): Promise<NaturalSearchResponse> {
  const payload: Record<string, string> = { description };
  if (mediaType && mediaType !== 'any') {
    payload.media_type = mediaType;
  }

  const resp = await apiFetch('/api/search/natural', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `Search failed (${resp.status})`);
  }

  return resp.json();
}
