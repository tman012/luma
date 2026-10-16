import { apiFetch } from '../apiClient';
/**
 * Taste API — explicit verdicts on watched films, and what to acquire next.
 *
 * See app/taste.py. The verdict is binary by design — a middle option is what
 * people pick to avoid deciding, and a shrug is not something an automation can
 * act on.
 */

export type Verdict = 'liked' | 'disliked';

export interface TasteFilm {
  tmdb_id: number;
  title: string;
  year: number | null;
  overview: string;
  poster_path: string;
  backdrop_path: string;
  runtime: number | null;
  rating: number;
  genres: string[];
}

export interface TasteSuggestion {
  tmdb_id: number;
  title: string;
  year: number | null;
  overview: string;
  poster_path: string;
  rating: number;
  votes: number;
  score: number;
  because: string[];
}

export interface TasteWatch {
  tmdb_id: number;
  title: string;
  year: number | null;
  watched_at: number;
  rating_key: string;
}

export async function fetchTastePending(hours = 24): Promise<{
  account_id: number;
  window_hours: number;
  /** The newest watch, or null once it has been answered. What the UI prompts
   *  on — an ignored question must not stack up behind newer ones. */
  latest: TasteWatch | null;
  /** Fuller unrated list. Diagnostics only. */
  pending: TasteWatch[];
}> {
  const resp = await fetch(`/api/taste/pending?hours=${hours}`);
  if (!resp.ok) throw new Error(`Failed to load recent watches (${resp.status})`);
  return resp.json();
}

export async function fetchTasteFilm(tmdbId: number): Promise<{
  tmdb_id: number;
  film: TasteFilm;
  verdict: Verdict | null;
  suggestions: TasteSuggestion[];
  pool?: number;
}> {
  const resp = await fetch(`/api/taste/film/${tmdbId}`);
  if (!resp.ok) throw new Error(`Failed to load film (${resp.status})`);
  return resp.json();
}

export async function submitVerdict(
  tmdbId: number,
  verdict: Verdict,
  title?: string,
  year?: number | null,
): Promise<{ ok: boolean; suggestions: TasteSuggestion[]; pool?: number }> {
  const resp = await apiFetch('/api/taste/rate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tmdb_id: tmdbId, verdict, title, year }),
  });
  if (!resp.ok) throw new Error(`Failed to save rating (${resp.status})`);
  return resp.json();
}

/** Stop asking about a film without recording a verdict. A dismissal is not an
 *  opinion, so it is stored separately from ratings — but it does suppress both
 *  the push and the in-app prompt. */
export async function dismissFilm(tmdbId: number): Promise<{ ok: boolean }> {
  const resp = await apiFetch('/api/taste/dismiss', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tmdb_id: tmdbId }),
  });
  return resp.json();
}

/* Acquisition is deliberately NOT here. Tapping a suggestion opens the standard
 * media modal, which already owns adding, ownership state and quality choice —
 * a second add path would drift from it. The POST /api/taste/grab endpoint
 * remains for the passive mode (auto-grab when a prompt goes unanswered), which
 * has no UI to route through. */
