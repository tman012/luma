/**
 * BrowseLibraryPage — route component for /browse.
 *
 * ?library=X absent → LibraryListView (library picker)
 * ?library=X present → BrowseGridView (poster grid)
 *
 * No header bar. Large floating back button top-left.
 */

import { useCallback, useMemo, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import { useUiDensity } from '@/hooks/useUiDensity';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { cn } from '@/utils/cn';
import { LibraryListView } from './browse/LibraryListView';
import { BrowseGridView } from './browse/BrowseGridView';
import { fetchMediaPaths } from '@/services/libraryBrowseApi';
import type { MediaPath } from '@/services/libraryBrowseApi';

export function BrowseLibraryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const d = useUiDensity();
  const isMobile = d.mode === 'compact';
  useBodyScroll();

  const libraryName = searchParams.get('library');

  const [paths, setPaths] = useState<MediaPath[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetchMediaPaths().then((p) => {
      if (!cancelled) setPaths(p);
    });
    return () => { cancelled = true; };
  }, []);

  const mediaType = useMemo(() => {
    if (!libraryName) return 'movie';
    const match = paths.find((p) => p.name === libraryName);
    if (match && match.type.toLowerCase().includes('tv')) return 'tv' as const;
    return 'movie' as const;
  }, [libraryName, paths]);

  const handleSelectLibrary = useCallback((name: string) => {
    setSearchParams({ library: name });
  }, [setSearchParams]);

  return (
    <div
      className="bg-kiosk-black text-white"
      style={{ minHeight: '100dvh' }}
    >
      {/* Fixed top bar: back chevron + centered title */}
      <div
        style={{
          position: 'fixed', top: 0, left: 0, right: 0, zIndex: 50,
          paddingTop: 'env(safe-area-inset-top, 0px)',
          background: 'var(--color-kiosk-black)',
        }}
      >
        <div style={{ height: 56, display: 'flex', alignItems: 'center', paddingLeft: 16, paddingRight: 16 }}>
          {libraryName && (
            <button
              type="button"
              onClick={() => setSearchParams({})}
              className="mr-2 text-[14px] text-white/50 hover:text-white/80 transition-colors"
            >
              Libraries /
            </button>
          )}
          <h1 className="text-[19px] font-semibold text-white truncate" style={{ margin: 0 }}>
            {libraryName ?? 'Libraries'}
          </h1>
        </div>
      </div>

      {/* Content */}
      <div className={cn(
        'mx-auto max-w-[1600px]',
        isMobile ? 'px-4 pb-8' : 'px-4 sm:px-6 sm:pb-20 md:px-8',
      )} style={{ paddingTop: 'calc(56px + env(safe-area-inset-top, 0px) + 16px)' }}>
        {libraryName ? (
          <BrowseGridView
            libraryName={libraryName}
            mediaType={mediaType}
            isMobile={isMobile}
          />
        ) : (
          <LibraryListView
            onSelectLibrary={handleSelectLibrary}
            isMobile={isMobile}
          />
        )}
      </div>
    </div>
  );
}
