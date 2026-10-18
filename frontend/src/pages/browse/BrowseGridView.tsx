/**
 * BrowseGridView — paginated poster grid for a selected library.
 *
 * Fetches items from /api/library/browse with sort, direction, genre params.
 * "Load more" button appends next batch. AlphabetSidebar visible only when
 * sort === 'title'. Card clicks open MediaDetailModal or TvDetailModal
 * based on library type.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router';
import { useModalHost, type ModalSource } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { cn } from '@/utils/cn';
import { fetchLibraryBrowse, fetchLibraryGenres } from '@/services/libraryBrowseApi';
import type { BrowseItem } from '@/services/libraryBrowseApi';
import { BrowsePosterCard } from './BrowsePosterCard';
import { BrowseGridFilters, type SortState } from './BrowseGridFilters';
import { AlphabetSidebar } from './AlphabetSidebar';

const PAGE_SIZE = 30;

interface BrowseGridViewProps {
  libraryName: string;
  /** 'movie' or 'tv' — determines which modal to open on card click. */
  mediaType: 'movie' | 'tv';
  isMobile: boolean;
}

export function BrowseGridView({ libraryName, mediaType, isMobile }: BrowseGridViewProps) {
  const navigate = useNavigate();
  const { openMovie, openTv, close } = useModalHost();

  const [items, setItems] = useState<BrowseItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [sort, setSort] = useState<SortState>({ key: 'added_date', direction: 'desc' });
  const [genre, setGenre] = useState('');
  const [genres, setGenres] = useState<string[]>([]);

  // Ref to the scrollable container for alphabet sidebar scroll-to
  const gridRef = useRef<HTMLDivElement>(null);

  // Fetch genres once for this library
  useEffect(() => {
    let cancelled = false;
    fetchLibraryGenres(libraryName).then((g) => {
      if (!cancelled) setGenres(g);
    });
    return () => { cancelled = true; };
  }, [libraryName]);

  // Fetch items when sort/genre changes (reset)
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setItems([]);
    fetchLibraryBrowse({
      library: libraryName,
      offset: 0,
      limit: PAGE_SIZE,
      sort: sort.key,
      direction: sort.direction,
      genre: genre || undefined,
    }).then((resp) => {
      if (!cancelled) {
        setItems(resp.items);
        setTotal(resp.total);
        setLoading(false);
      }
    });
    return () => { cancelled = true; };
  }, [libraryName, sort, genre]);

  // Load more
  const handleLoadMore = useCallback(async () => {
    setLoadingMore(true);
    const resp = await fetchLibraryBrowse({
      library: libraryName,
      offset: items.length,
      limit: PAGE_SIZE,
      sort: sort.key,
      direction: sort.direction,
      genre: genre || undefined,
    });
    setItems((prev) => [...prev, ...resp.items]);
    setTotal(resp.total);
    setLoadingMore(false);
  }, [libraryName, items.length, sort, genre]);

  // Modal click handler
  const makePersonSearch = useCallback((name: string, personId?: number) => {
    close();
    const params = new URLSearchParams({ q: name, type: 'people' });
    if (personId) params.set('personId', String(personId));
    navigate(`/?${params.toString()}`);
  }, [close, navigate]);

  const handleCardClick = useCallback((item: BrowseItem) => {
    openModalForItem(
      { openMovie, openTv },
      {
        mediaType,
        tmdbId: item.tmdb_id,
        ratingKey: item.rating_key,
      },
      'browse' as ModalSource,
      { onPersonSearch: makePersonSearch },
    );
  }, [openMovie, openTv, mediaType, makePersonSearch]);

  // Alphabet sidebar: scroll to first item with matching letter
  const allTitles = useMemo(() => items.map((i) => i.title), [items]);

  const handleLetterClick = useCallback((letter: string) => {
    const target = items.findIndex((item) => {
      const first = (item.title[0] ?? '').toUpperCase();
      if (letter === '#') return !/[A-Z]/.test(first);
      return first === letter;
    });
    if (target < 0) return;

    // Find the card DOM element by data attribute
    const el = gridRef.current?.querySelector(`[data-index="${target}"]`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, [items]);

  const hasMore = items.length < total;

  return (
    <div>
      {/* Sticky filters bar — pins below the fixed header on scroll */}
      <div
        className="sticky z-40 -mx-4 bg-kiosk-black px-4 pb-3 pt-1"
        style={{ top: 'calc(3.5rem + env(safe-area-inset-top, 0px))' }}
      >
        <BrowseGridFilters
          sort={sort}
          onSortChange={setSort}
          genre={genre}
          onGenreChange={setGenre}
          genres={genres}
          totalCount={total}
          loadedCount={items.length}
        />
      </div>

      {/* Alphabet sidebar — title sort only */}
      {sort.key === 'title' && items.length > 0 && (
        <AlphabetSidebar titles={allTitles} onLetterClick={handleLetterClick} />
      )}

      {/* Grid */}
      {loading ? (
        <div className={cn(
          'grid gap-3 sm:gap-4 md:gap-5',
          isMobile
            ? 'grid-cols-3'
            : 'grid-cols-[repeat(auto-fill,minmax(180px,1fr))]',
        )}>
          {Array.from({ length: isMobile ? 9 : 20 }).map((_, i) => (
            <div key={i}>
              <div className="aspect-[2/3] animate-pulse rounded-xl bg-glass-5" />
              <div className="mt-2 h-4 w-3/4 animate-pulse rounded bg-glass-5" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="flex h-48 items-center justify-center">
          <p className="text-[14px] text-text-muted">No items found</p>
        </div>
      ) : (
        <div ref={gridRef}>
          <div className={cn(
            'grid gap-3 sm:gap-4 md:gap-5',
            isMobile
              ? 'grid-cols-3'
              : 'grid-cols-[repeat(auto-fill,minmax(180px,1fr))]',
            sort.key === 'title' ? 'pr-8 sm:pr-10' : '',
          )}>
            {items.map((item, idx) => (
              <div key={`${item.rating_key}-${idx}`} data-index={idx} className="min-w-0">
                <BrowsePosterCard
                  title={item.title}
                  year={item.year}
                  posterUrl={item.poster_url}
                  onClick={() => handleCardClick(item)}
                />
              </div>
            ))}
          </div>

          {/* Load more */}
          {hasMore && (
            <div className="mt-8 flex justify-center">
              <button
                type="button"
                onClick={handleLoadMore}
                disabled={loadingMore}
                className={cn(
                  'rounded-xl px-6 py-3 text-[15px] font-medium transition-colors',
                  'bg-glass-10 hover:bg-glass-20 text-text-primary',
                  loadingMore && 'opacity-50 cursor-not-allowed',
                )}
              >
                {loadingMore ? 'Loading...' : 'Load more'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
