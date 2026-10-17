import { readFileSync } from 'fs';
import { resolve } from 'path';
import { render, cleanup, fireEvent, renderHook, waitFor, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchAllResults, trackerStatusText, type TrackerStatus } from '@/services/allSourcesApi';
import { TrackerStatusNotice } from '@/components/media/sourcesTrackerStatus';
import { useMovieSourcesData } from '@/components/media/MovieDetailModal/useMovieSourcesData';
import { useTvSourcesData } from '@/components/media/TvDetailModal/useTvSourcesData';

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
    ['components/media/MovieDetailModal/SourcesPanel.tsx', 3],
    ['components/media/TvDetailModal/TvSourcesPanel.tsx', 4],
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

describe('Designer spec: partial-failure line (BHD failed, IPT has rows)', () => {
  it('14px/500 white 70% text, amber dot from the theme, glass Retry pill on the same line', async () => {
    const { TrackerStatusBar } = await import('@/components/media/sourcesTrackerStatus');
    const { container } = render(<TrackerStatusBar trackers={[{ name: 'BHD', status: 'unavailable' }]} onRetry={() => {}} phone />);
    const line = container.querySelector('[data-sources-state="trackers-partial"]') as HTMLElement;
    expect(line.style.display).toBe('flex');
    expect(line.style.marginBottom || line.style.margin).toContain('12px');
    const text = container.querySelector('[data-tracker-status]') as HTMLElement;
    expect(text.style.fontSize).toBe('14px');
    expect(text.style.fontWeight).toBe('500');
    expect(text.style.color).toBe('rgba(255, 255, 255, 0.7)');
    const dot = container.querySelector('[data-status-dot]') as HTMLElement;
    expect(dot.style.width).toBe('8px');
    expect(dot.style.background).toBe('var(--color-warning)');
    const btn = container.querySelector('[data-sources-retry]') as HTMLElement;
    expect(btn.style.height).toBe('44px');
    expect(btn.style.borderRadius).toBe('999px');
    expect(btn.style.background).toBe('rgba(255, 255, 255, 0.08)');
    // the text block grows, so Retry sits at the right end of the line
    expect((line.firstElementChild as HTMLElement).style.flex).toBe('1 1 auto');
    expect(line.lastElementChild).toBe(btn);
    expect(container.textContent).not.toMatch(/HTTP|\d{3}/);
  });

  it('Retry is 32px on desktop and disabled while it runs', async () => {
    const { TrackerStatusBar } = await import('@/components/media/sourcesTrackerStatus');
    const { container } = render(<TrackerStatusBar trackers={[{ name: 'BHD', status: 'unavailable' }]} onRetry={() => {}} retrying />);
    const btn = container.querySelector('[data-sources-retry]') as HTMLButtonElement;
    expect(btn.style.height).toBe('32px');
    expect(btn.disabled).toBe(true);
  });

  it('the theme has the amber token', () => {
    expect(read('index.css')).toMatch(/--color-warning: #f59e0b;/);
  });

  it('phone panels use the 44px pill, desktop and TV panels 32px', () => {
    for (const p of ['components/media/MovieDetailModal/SourcesPanel.tsx', 'components/media/TvDetailModal/TvSourcesPanel.tsx']) {
      const src = read(p);
      expect(src.match(/<TrackerStatusBar[^>]*\bphone\b/g)).toHaveLength(p.includes('Tv') ? 3 : 2);
    }
    expect(read('components/media/MovieDetailModal/ExpandedLayout.tsx')).not.toMatch(/<TrackerStatusBar[^>]*\bphone\b/);
  });

  it('a successful Retry keeps the rows on screen (no loading state) and only the line goes away', async () => {
    let bhdOk = false;
    const row = (t: string, ix: string, g: string) => ({ title: t, indexer: ix, indexer_id: 1, size: 1, seeders: 3, download_url: `/api/trackers/release/${g}`, guid: g });
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      if (String(u).includes('/api/sources/all-results')) {
        const rel = [row('Keep.Rows.2021.1080p.WEB-DL-GRP', 'IPTorrents', 'i1')];
        if (bhdOk) rel.push(row('Keep.Rows.2021.2160p.Remux-GRP', 'BHD', 'b1'));
        return new Response(JSON.stringify({ releases: rel, trackers: bhdOk ? [] : [{ name: 'BHD', status: 'unavailable' }] }), { status: 200 });
      }
      return new Response(JSON.stringify({ releases: [] }), { status: 200 });
    }));
    const opts = { tmdbId: 21, title: 'Keep Rows', year: 2021 };
    const { result } = renderHook(() => useMovieSourcesData(opts));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.releases).toHaveLength(1);
    expect(result.current.trackers).toHaveLength(1);
    bhdOk = true;
    const seen: boolean[] = [];
    act(() => result.current.retry());
    seen.push(result.current.loading);
    expect(result.current.retrying).toBe(true);
    expect(result.current.releases).toHaveLength(1);       // the rows stay while Retry runs
    await waitFor(() => expect(result.current.retrying).toBe(false));
    expect(seen).toEqual([false]);
    expect(result.current.loading).toBe(false);
    expect(result.current.trackers).toEqual([]);
    expect(result.current.releases).toHaveLength(2);
  });
});

describe('TV: a successful Retry keeps the rows', () => {
  it('no loading state while Retry runs; the status clears after', async () => {
    let ok = false;
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      if (String(u).includes('/api/sources/all-results')) {
        const rel = [{ title: 'Keep.Show.S01.1080p.WEB-DL-GRP', indexer: 'IPTorrents', indexer_id: 1, size: 1, seeders: 3, download_url: '/api/trackers/release/t1', guid: 't1' }];
        return new Response(JSON.stringify({ releases: rel, trackers: ok ? [] : [{ name: 'BHD', status: 'unavailable' }] }), { status: 200 });
      }
      return new Response(JSON.stringify({ success: false, releases: [] }), { status: 200 });
    }));
    const opts = { target: { type: 'season' as const, season: 1 }, tvdbId: 0, tmdbId: 31, title: 'Keep Show' };
    const { result } = renderHook(() => useTvSourcesData(opts));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(result.current.trackers).toHaveLength(1));
    expect(result.current.releases).toHaveLength(1);
    ok = true;
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.retrying).toBe(true));
    expect(result.current.loading).toBe(false);
    expect(result.current.releases).toHaveLength(1);
    await waitFor(() => expect(result.current.retrying).toBe(false));
    expect(result.current.trackers).toEqual([]);
    expect(result.current.loading).toBe(false);
  });
});
