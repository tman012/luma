import { useState, useEffect } from 'react';
import { apiFetch, mapHeroMovie } from '@/services/api';
import type { HeroMovie, HeroContentResponse, LibraryRecentResponse, KioskSettings } from '@/services/types';

export function useHeroContent() {
  const [movies, setMovies] = useState<HeroMovie[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [backdropSource, setBackdropSource] = useState<'plex' | 'tmdb'>('plex');

  useEffect(() => {
    let cancelled = false;

    async function fetchHero() {
      // Fetch kiosk settings (backdrop source) in parallel with hero content
      try {
        const ks = await apiFetch<KioskSettings>('/api/kiosk/settings');
        if (!cancelled && ks.backdrop_source) {
          setBackdropSource(ks.backdrop_source);
        }
      } catch {
        // ignore — default to plex
      }

      try {
        const data = await apiFetch<HeroContentResponse>('/api/hero/content');
        if (!cancelled && data.success && data.movies.length > 0) {
          setMovies(data.movies.map(mapHeroMovie));
          setError(null);
          return;
        }
      } catch {
        // Fall through to fallback
      }

      // Fallback to library recent
      try {
        const fallback = await apiFetch<LibraryRecentResponse>(
          '/api/library/recent?limit=6',
        );
        if (!cancelled) {
          setMovies((fallback.movies ?? []).map(mapHeroMovie));
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load hero content');
        }
      }

      if (!cancelled) {
        setLoading(false);
      }
    }

    fetchHero().finally(() => {
      if (!cancelled) setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, []);

  return { movies, loading, error, backdropSource };
}
