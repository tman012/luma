/**
 * LibraryListView — library picker showing media paths grouped by type.
 *
 * Fetches media.paths from GET /api/settings instantly, then loads counts
 * in the background via /api/library/browse?limit=0 per library.
 * Click row -> onSelectLibrary(name).
 */

import { useState, useEffect, useMemo } from 'react';
import { cn } from '@/utils/cn';
import { fetchMediaPaths, fetchLibraryBrowse } from '@/services/libraryBrowseApi';
import type { MediaPath } from '@/services/libraryBrowseApi';

interface LibraryListViewProps {
  onSelectLibrary: (name: string) => void;
  isMobile: boolean;
}

interface LibraryRow {
  name: string;
  type: string;
  count: number | null;
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function MovieIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-text-muted">
      <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
      <line x1="7" y1="2" x2="7" y2="22" />
      <line x1="17" y1="2" x2="17" y2="22" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <line x1="2" y1="7" x2="7" y2="7" />
      <line x1="2" y1="17" x2="7" y2="17" />
      <line x1="17" y1="7" x2="22" y2="7" />
      <line x1="17" y1="17" x2="22" y2="17" />
    </svg>
  );
}

function TvIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-text-muted">
      <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
      <polyline points="17 2 12 7 7 2" />
    </svg>
  );
}

function ChevronRight() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-white/20">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function LibraryListView({ onSelectLibrary, isMobile }: LibraryListViewProps) {
  const [paths, setPaths] = useState<MediaPath[]>([]);
  const [counts, setCounts] = useState<Map<string, number>>(new Map());
  const [loading, setLoading] = useState(true);

  // Phase 1: Load library list instantly from settings
  useEffect(() => {
    let cancelled = false;
    fetchMediaPaths().then((p) => {
      if (!cancelled) {
        setPaths(p);
        setLoading(false);
      }
    });
    return () => { cancelled = true; };
  }, []);

  // Phase 2: Load counts in background per-library (fast — browse with limit=0)
  useEffect(() => {
    if (paths.length === 0) return;
    let cancelled = false;

    // Fire all count requests in parallel
    Promise.all(
      paths.map(async (p) => {
        const resp = await fetchLibraryBrowse({ library: p.name, limit: 0 });
        return { name: p.name, total: resp.total };
      }),
    ).then((results) => {
      if (cancelled) return;
      const map = new Map<string, number>();
      for (const r of results) {
        map.set(r.name, r.total);
      }
      setCounts(map);
    });

    return () => { cancelled = true; };
  }, [paths]);

  // Build rows with counts
  const { movieLibraries, tvLibraries } = useMemo(() => {
    const movies: LibraryRow[] = [];
    const tv: LibraryRow[] = [];

    for (const p of paths) {
      const count = counts.get(p.name) ?? null;
      const row: LibraryRow = { name: p.name, type: p.type, count };
      if (p.type.toLowerCase().includes('tv')) {
        tv.push(row);
      } else {
        movies.push(row);
      }
    }

    return { movieLibraries: movies, tvLibraries: tv };
  }, [paths, counts]);

  if (loading) {
    return (
      <div className="space-y-6">
        {[1, 2].map((g) => (
          <div key={g}>
            <div className="mb-3 h-5 w-24 animate-pulse rounded bg-glass-5" />
            <div className="rounded-xl bg-white/5 overflow-hidden">
              {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 px-4 py-4">
                  <div className="h-5 w-5 animate-pulse rounded bg-glass-5" />
                  <div className="h-5 w-40 animate-pulse rounded bg-glass-5" />
                  <div className="ml-auto h-4 w-12 animate-pulse rounded bg-glass-5" />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (paths.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center">
        <p className="text-[14px] text-text-muted">No libraries configured</p>
      </div>
    );
  }

  const renderSection = (title: string, rows: LibraryRow[], icon: React.ReactNode) => {
    if (rows.length === 0) return null;
    return (
      <div className="mb-8">
        <h2 className={cn(
          'mb-4 font-semibold text-white/60',
          isMobile ? 'text-[14px] px-1' : 'text-[15px]',
        )}>
          {title}
        </h2>
        <div className="rounded-2xl bg-white/5 overflow-hidden divide-y divide-white/5">
          {rows.map((row) => (
            <button
              key={row.name}
              type="button"
              onClick={() => onSelectLibrary(row.name)}
              className="flex w-full items-center gap-4 px-5 py-5 text-left transition-colors hover:bg-white/5 active:bg-white/8"
            >
              {icon}
              <span className="flex-1 truncate text-[18px] font-medium text-text-primary">
                {row.name}
              </span>
              {row.count != null ? (
                <span className="text-[15px] text-text-muted">
                  {row.count.toLocaleString()}
                </span>
              ) : (
                <span className="h-5 w-12 animate-pulse rounded bg-glass-5" />
              )}
              <ChevronRight />
            </button>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div>
      {renderSection('Movies', movieLibraries, <MovieIcon />)}
      {renderSection('TV shows', tvLibraries, <TvIcon />)}
    </div>
  );
}
