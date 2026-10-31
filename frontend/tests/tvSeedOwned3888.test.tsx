/** Reviewer B1 (3888ffe8): the tap-time ownership seed only stands in until the TV details load. A stale
 *  "owned" card orb must not keep Watch in place of Add once the details say the show isn't owned. */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { is_admin: true } }) }));
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => new Proxy({}, { get: () => () => () => {} }) }));
vi.mock('@/hooks/useImdbRatings', () => ({ useImdbRatingsState: () => ({ ratings: null, loading: false }), useImdbRatings: () => null }));
vi.mock('@/hooks/useTitleReveal', () => ({ useTitleReveal: () => 'text', logoInput: () => null, titleTextStyle: () => ({ display: 'block' }) }));
vi.mock('@/services/modalPrefetch', async (orig) => ({
  ...(await orig<typeof import('@/services/modalPrefetch')>()),
  tapShell: () => ({ title: 'Andor', poster: null, owned: true }),
}));

import { CompactLayout } from '@/components/media/TvDetailModal/CompactLayout';

afterEach(cleanup);

const base = (over: Record<string, unknown>) => ({
  show: { title: 'Andor', year: 2022, isOwned: false, backdropUrl: null, posterUrl: null, clearLogoUrl: null, overview: 'x', genres: [], cast: [], crew: [], creators: [], networks: [] },
  seasons: [], ownership: null, showOwned: false, sonarrStatus: null, loading: false, error: null, ultraBlurColors: null,
  selectedSeason: null, setSelectedSeason: () => {}, phase: 'visible', onClose: () => {}, showAddToSonarr: false,
  setShowAddToSonarr: () => {}, onPersonSearch: () => {}, grabToast: null, watchlistToast: null, onSonarrAdded: () => {},
  onSourcesGrabbed: () => {}, tvdbId: 393189, resolvedTmdbId: 83867, showSources: false, setShowSources: () => {},
  showPlayback: false, setShowPlayback: () => {}, episodeContext: null, episodeData: null, episodeLoading: false,
  showFullShow: false, setShowFullShow: () => {}, onEpisodeClick: () => {}, playbackRatingKey: null, playbackTitle: null,
  onPlayEpisode: () => {}, plexSeasons: [], shuffleTarget: null, setShuffleTarget: () => {},
  ...over,
});

describe('TV phone sheet: the ownership seed yields to the loaded details', () => {
  it('seed owned, details not owned: Add/monitor, no Watch', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    render(<CompactLayout {...(base({}) as any)} />);
    expect(screen.getByTestId('tv-add-watchlist')).toBeTruthy();
    expect(document.querySelector('[data-owned-watch]')).toBeNull();
  });
  it('details owned still shows Watch', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    render(<CompactLayout {...(base({ show: { ...base({}).show, isOwned: true, ratingKey: '5' } }) as any)} />);
    expect(document.querySelector('[data-owned-watch]')).not.toBeNull();
    expect(screen.queryByTestId('tv-add-watchlist')).toBeNull();
  });
});
