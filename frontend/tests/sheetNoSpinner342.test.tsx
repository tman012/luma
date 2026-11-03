/** No-spinner (Taylor 2026-11-02): the movie and TV detail sheets, phone and desktop, show no spinner,
 *  loading ring or circular progress while they load; the pre-reveal surface is all there is. */
import { act, render, cleanup } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const layoutMode = vi.hoisted(() => ({ mode: 'compact' as 'compact' | 'expanded' }));
vi.mock('@/contexts/LayoutModeContext', async (orig) => ({
  ...(await orig<typeof import('@/contexts/LayoutModeContext')>()),
  useLayoutMode: () => ({ mode: layoutMode.mode, isCompact: layoutMode.mode === 'compact', isExpanded: layoutMode.mode === 'expanded', layoutMode: layoutMode.mode }),
}));
vi.mock('@/contexts/AuthContext', async (orig) => ({
  ...(await orig<typeof import('@/contexts/AuthContext')>()),
  useAuth: () => ({ implicit: false, authenticated: true, user: { id: 1, username: 't', isAdmin: true }, isAdmin: true }),
}));
vi.mock('@/contexts/ModalHostContext', async (orig) => ({
  ...(await orig<typeof import('@/contexts/ModalHostContext')>()),
  useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn(), registerBackHandler: vi.fn(() => () => {}), closeAll: vi.fn() }),
}));
vi.mock('@/hooks/useMovieDetail', () => ({
  useMovieDetail: () => ({ detail: null, ratings: null, ratingsLoading: true, ratingsSettled: false, loading: true, error: null, trailerKey: null, ultraBlurColors: null, resolvedTmdbId: undefined }),
}));

import { MovieDetailModal } from '@/components/media/MovieDetailModal';
import { TvDetailModal } from '@/components/media/TvDetailModal';

const SPINNER = '[data-sheet-spinner], [role="progressbar"], .spinner, .animate-spin';
const spinning = () => Array.from(document.querySelectorAll<HTMLElement>('*')).filter((el) => /spin/i.test(el.style?.animation ?? ''));
const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');

beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {}))); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('no spinner in either detail sheet while loading', () => {
  for (const mode of ['compact', 'expanded'] as const) {
    it(`movie and TV sheets (${mode}): nothing spins before or past the deadline`, async () => {
      layoutMode.mode = mode;
      for (const el of [<MovieDetailModal key="m" tmdbId={329865} onClose={() => {}} />, <TvDetailModal key="t" tmdbId={95396} onClose={() => {}} />]) {
        render(el);
        for (const ms of [0, 200, 1100, 3000]) {
          await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
          expect(document.querySelector(SPINNER)).toBeNull();
          expect(spinning()).toEqual([]);
        }
        cleanup();
      }
    });
  }
  it('no loading/episode spinner markup is left in the four layouts', () => {
    for (const k of ['MovieDetailModal/CompactLayout', 'MovieDetailModal/ExpandedLayout', 'TvDetailModal/CompactLayout', 'TvDetailModal/ExpandedLayout']) {
      const s = read(`components/media/${k}.tsx`);
      expect(s, k).not.toMatch(/data-sheet-spinner/);
      expect(s, k).not.toMatch(/animation: '(webModalSpin|mobileTvModalSpin|tvExpandedSpin) 0\.8s linear infinite',?\s*\}\}\s*\/?>\s*(<\/div>\s*)?\)\}/);
    }
  });
});
