import { readFileSync } from 'fs';
import { resolve } from 'path';
import { render, cleanup, fireEvent, renderHook, waitFor, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchAllResults, trackerStatusText, type TrackerStatus } from '@/services/allSourcesApi';
import { TrackerStatusNotice } from '@/components/media/sourcesTrackerStatus';
import { useMovieSourcesData } from '@/components/media/MovieDetailModal/useMovieSourcesData';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('per-tracker status instead of the empty state (PM 1)', () => {
  it('short status text per tracker', () => {
    expect(trackerStatusText({ name: 'IPTorrents', status: 'auth' })).toBe('IPTorrents sign-in failed');
    expect(trackerStatusText({ name: 'TL', status: 'rate' })).toBe('TL search limit reached');
    expect(trackerStatusText({ name: 'TL', status: 'unavailable' })).toBe('TL unavailable');
  });

  it('fetchAllResults hands over the trackers list', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      releases: [], trackers: [{ name: 'IPTorrents', status: 'auth' }, { name: 'X', status: 'weird' }, { bad: 1 }],
    }), { status: 200 })));
    let got: TrackerStatus[] = [];
    await fetchAllResults('Dune', 'movie', null, undefined, undefined, false, (t) => { got = t; });
    expect(got).toEqual([{ name: 'IPTorrents', status: 'auth' }, { name: 'X', status: 'unavailable' }]);
  });

  it('renders status lines and Retry only', () => {
    const onRetry = vi.fn();
    const { container, getByText } = render(
      <TrackerStatusNotice trackers={[{ name: 'IPTorrents', status: 'auth' }, { name: 'TL', status: 'rate' }]} onRetry={onRetry} />,
    );
    expect(container.textContent).toBe('IPTorrents sign-in failedTL search limit reachedRetry');
    fireEvent.click(getByText('Retry'));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('the movie hook exposes trackers and Retry re-asks with refresh=1', async () => {
    const urls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      const s = String(u);
      if (s.includes('/api/sources/all-results')) {
        urls.push(s);
        return new Response(JSON.stringify({ releases: [], trackers: [{ name: 'IPTorrents', status: 'auth' }] }), { status: 200 });
      }
      return new Response(JSON.stringify({ releases: [] }), { status: 200 });
    }));
    const { result } = renderHook(() => useMovieSourcesData({ tmdbId: 9, title: 'Retry Movie', year: 2021 }));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.trackers).toEqual([{ name: 'IPTorrents', status: 'auth' }]);
    expect(urls).toHaveLength(1);
    expect(urls[0]).not.toContain('refresh=1');
    act(() => result.current.retry());
    await waitFor(() => expect(urls).toHaveLength(2));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(urls[1]).toContain('refresh=1');
  });

  it.each([
    ['components/media/MovieDetailModal/ExpandedLayout.tsx', 1],
    ['components/media/MovieDetailModal/SourcesPanel.tsx', 2],
    ['components/media/TvDetailModal/TvSourcesPanel.tsx', 2],
  ])('%s shows the status on every empty state', (p, n) => {
    expect(read(p).split('<TrackerStatusNotice').length - 1).toBe(n);
  });
});

describe('Override pill overlap (PM 3)', () => {
  it('desktop movie logo/title is hidden while Sources is open', () => {
    const src = read('components/media/MovieDetailModal/ExpandedLayout.tsx');
    const logo = src.slice(src.indexOf('data-modal-logo') - 900, src.indexOf('data-modal-logo'));
    expect(logo).toContain("opacity: phase === 'visible' && !showSourcesShelf ? 1 : 0");
    expect(logo).toContain("visibility: showSourcesShelf ? 'hidden' : undefined");
  });
});

describe('Tester 4049342: partial tracker failure and minimal mode', () => {
  it('a status line with Retry above the rows (Bug 3)', async () => {
    const { TrackerStatusBar } = await import('@/components/media/sourcesTrackerStatus');
    const onRetry = vi.fn();
    const { container, getByText } = render(<TrackerStatusBar trackers={[{ name: 'BHD', status: 'unavailable' }]} onRetry={onRetry} />);
    expect(container.textContent).toBe('BHD unavailableRetry');
    fireEvent.click(getByText('Retry'));
    expect(onRetry).toHaveBeenCalledTimes(1);
    cleanup();
    expect(render(<TrackerStatusBar trackers={[]} onRetry={onRetry} />).container.textContent).toBe('');
  });

  it.each([
    ['components/media/MovieDetailModal/ExpandedLayout.tsx', 1],
    ['components/media/MovieDetailModal/SourcesPanel.tsx', 2],
    ['components/media/TvDetailModal/TvSourcesPanel.tsx', 2],
  ])('%s shows it next to the rows', (p, n) => {
    expect(read(p).split('<TrackerStatusBar').length - 1).toBe(n);
  });

  it('the movie hook keeps rows and trackers together', async () => {
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      if (String(u).includes('/api/sources/all-results')) {
        return new Response(JSON.stringify({
          releases: [{ title: 'Partial.Movie.2021.1080p.WEB-DL-GRP', indexer: 'IPTorrents', indexer_id: 1, size: 1, seeders: 3, download_url: '/api/trackers/release/x', guid: 'p1' }],
          trackers: [{ name: 'BHD', status: 'unavailable' }],
        }), { status: 200 });
      }
      return new Response(JSON.stringify({ releases: [] }), { status: 200 });
    }));
    const opts = { tmdbId: 11, title: 'Partial Movie', year: 2021 };
    const { result } = renderHook(() => useMovieSourcesData(opts));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.releases).toHaveLength(1);
    expect(result.current.trackers).toEqual([{ name: 'BHD', status: 'unavailable' }]);
  });

  it('minimal mode never calls /api/radarr/movie/releases', async () => {
    const urls: string[] = [];
    (window as unknown as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    try {
      vi.stubGlobal('fetch', vi.fn(async (u: string) => {
        urls.push(String(u));
        return new Response(JSON.stringify({ releases: [], trackers: [] }), { status: 200 });
      }));
      const opts = { tmdbId: 12, title: 'Minimal Movie', year: 2020 };
      const { result } = renderHook(() => useMovieSourcesData(opts));
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(urls.some((u) => u.includes('/api/sources/all-results'))).toBe(true);
      expect(urls.some((u) => u.includes('/api/radarr'))).toBe(false);
      expect(result.current.error).toBeFalsy();
    } finally {
      delete (window as unknown as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__;
    }
  });

  it('minimal mode: a tracker search that cannot be reached is an error, not "No sources"', async () => {
    (window as unknown as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    try {
      vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network'); }));
      const opts = { tmdbId: 13, title: 'Minimal Fail', year: 2020 };
      const { result } = renderHook(() => useMovieSourcesData(opts));
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.error).toBeTruthy();
    } finally {
      delete (window as unknown as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__;
    }
  });
});
