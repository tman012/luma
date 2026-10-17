import { readFileSync } from 'fs';
import { resolve } from 'path';
import { render, cleanup, fireEvent, renderHook, waitFor, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchAllResults, trackerStatusText, type TrackerStatus } from '@/services/allSourcesApi';
import { TrackerStatusLine, TrackerStatusNotice } from '@/components/media/sourcesTrackerStatus';
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

describe('phone TV list, partial failure (Designer)', () => {
  it('one line with every failed tracker, nothing when all answered', () => {
    const { container } = render(<TrackerStatusLine trackers={[{ name: 'IPTorrents', status: 'auth' }, { name: 'TL', status: 'rate' }]} />);
    expect(container.textContent).toBe('IPTorrents sign-in failed · TL search limit reached');
    cleanup();
    const empty = render(<TrackerStatusLine trackers={[]} />);
    expect(empty.container.textContent).toBe('');
  });

  it('the "Select type" heading stays, with the status line right under it', () => {
    const src = read('components/media/TvDetailModal/TvSourcesPanel.tsx');
    const at = src.indexOf('Select type');
    const after = src.slice(at, at + 200);
    expect(after).toMatch(/^Select type\s*<\/p>\s*<TrackerStatusLine trackers=\{trackers\} \/>/);
  });
});
