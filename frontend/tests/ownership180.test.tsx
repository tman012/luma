import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, describe, expect, it, vi } from 'vitest';

const batch = vi.fn();
vi.mock('@/services/searchApi', () => ({ batchCheckOwnership: (...a: unknown[]) => batch(...a) }));
vi.mock('@/services/modalPrefetch', () => ({ scheduleWarm: () => () => {}, warmDetail: () => Promise.resolve() }));

import { HomeRails } from '@/components/home/Rails';
import { cardOrb, plexOwned } from '@/components/home/railOwnership';

const read = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const orbOf = (title: string) => screen.getByRole('button', { name: title }).querySelector('[data-owned-orb]');

afterEach(() => { cleanup(); batch.mockReset(); });

const CW = [{ key: '10', type: 'episode', title: 'Severance', subtitle: 'S1 E2', poster: null, progress: 0.25 }];
const RAILS = [
  { id: '1', title: 'Movies', type: 'movie', items: [{ key: '21', type: 'movie', title: 'Heat', subtitle: '1995', poster: null }] },
  { id: '6', title: 'Recently Released', type: 'movie', items: [{ key: '601', type: 'movie', title: 'Sinners', subtitle: '2025', poster: null }] },
  { id: '4', title: '4K', type: 'movie', items: [{ key: '401', type: 'movie', title: 'Oppenheimer', subtitle: '2023', poster: null }] },
  { id: 'trakt_trending_movies', title: 'Trending movies', type: 'movie', items: [
    { key: 'tmdb-movie-1', type: 'movie', title: 'Arrival', subtitle: '2016', poster: null, tmdb_id: 329865 },
    { key: 'tmdb-movie-2', type: 'movie', title: 'Nope', subtitle: '2022', poster: null, tmdb_id: 762504 },
  ] },
  { id: 'trakt_trending_shows', title: 'Trending shows', type: 'show', items: [
    { key: 'tmdb-show-3', type: 'show', title: 'Andor', subtitle: '2022', poster: null, tmdb_id: 83867 },
  ] },
];

describe('owned indicators on Home rails (prod orb, prod placement)', () => {
  it('shows no orb on all-owned library rails, as prod (v181), and asks nothing', async () => {
    batch.mockResolvedValue({});
    render(<HomeRails cw={CW} rails={RAILS.slice(0, 3)} onOpen={() => {}} />);
    for (const t of ['Severance', 'Heat', 'Sinners', 'Oppenheimer']) expect(orbOf(t)).toBeNull();
    expect(document.querySelectorAll('[data-owned-orb]').length).toBe(0);
    expect(batch).not.toHaveBeenCalled();
  });

  it('resolves Trakt cards with prod\'s batch check: owned green, not owned red, no orb before', async () => {
    let done: (v: unknown) => void = () => {};
    batch.mockReturnValue(new Promise(r => { done = r; }));
    render(<HomeRails cw={[]} rails={RAILS.slice(3)} onOpen={() => {}} />);
    expect(orbOf('Arrival')).toBeNull();
    expect(batch).toHaveBeenCalledTimes(1);
    const payload = batch.mock.calls[0][0];
    expect(payload.movies).toEqual([{ title: 'Arrival', year: 2016, tmdb_id: 329865 }, { title: 'Nope', year: 2022, tmdb_id: 762504 }]);
    expect(payload.tv).toEqual([{ title: 'Andor', year: 2022, tmdb_id: 83867 }]);
    done({ 'movie:arrival_2016': { owned: true, rating_key: 102 }, 'movie:nope_2022': { owned: false },
      'tv:andor_2022': { owned: true, rating_key: 203, owner_type: 'shared' } });
    await waitFor(() => expect(orbOf('Arrival')).not.toBeNull());
    expect(orbOf('Arrival')?.getAttribute('data-owned-orb')).toBe('owned');
    expect(orbOf('Nope')?.getAttribute('data-owned-orb')).toBe('missing');
    expect(orbOf('Nope')?.querySelector('span')?.className).toContain('bg-red-400');
    expect(orbOf('Andor')?.getAttribute('data-owned-orb')).toBe('shared');
    expect(orbOf('Andor')?.querySelector('span')?.className).toContain('bg-blue-400');
  });

  it('a library card next to a mixed rail still shows no orb', async () => {
    batch.mockResolvedValue({ 'movie:arrival_2016': { owned: true }, 'movie:nope_2022': { owned: false }, 'tv:andor_2022': { owned: true } });
    render(<HomeRails cw={CW} rails={RAILS} onOpen={() => {}} />);
    await waitFor(() => expect(orbOf('Arrival')).not.toBeNull());
    for (const t of ['Severance', 'Heat', 'Sinners', 'Oppenheimer']) expect(orbOf(t)).toBeNull();
  });

  it('uses prod\'s getOrbColor and orb geometry', () => {
    expect(cardOrb(undefined)).toBeNull();
    expect(cardOrb({ owned: true, ownerType: 'self' })?.bg).toBe('bg-green-400');
    expect(plexOwned({ key: '1', type: 'movie', title: 'X', subtitle: '', poster: null })).toEqual({ owned: true, ownerType: 'self' });
    expect(plexOwned({ key: 't', type: 'movie', title: 'X', subtitle: '', poster: null, tmdb_id: 5 })).toBeNull();
    const src = read('components/home/railOwnership.tsx');
    expect(src).toContain('absolute left-3 top-3');
    expect(src).toContain('block h-3 w-3 rounded-full');
  });
});

describe('v180 owned Play/Watch is display-only in minimal', () => {
  it('keeps the owned Play/Watch in every layout under the minimal action hide', () => {
    for (const f of ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/MovieDetailModal/ExpandedLayout.tsx',
      'components/media/TvDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/ExpandedLayout.tsx']) {
      expect(read(f)).toMatch(/data-modal-keep=""\s+data-owned-watch=""/);
    }
    expect(read('index.css')).toContain(':not([data-modal-sources]):not([data-modal-keep])');
  });
  it('labels stay prod\'s: Play on the desktop movie popup, Watch elsewhere', () => {
    expect(read('components/media/MovieDetailModal/ExpandedLayout.tsx')).toMatch(/data-owned-watch[\s\S]{0,900}label="Play"/);
    expect(read('components/media/MovieDetailModal/CompactLayout.tsx')).toMatch(/data-owned-watch[\s\S]{0,1200}Watch\s*<\/button>/);
    expect(read('components/media/TvDetailModal/ExpandedLayout.tsx')).toMatch(/data-owned-watch[\s\S]{0,900}label="Watch"/);
  });
  it('v219: playback is on in minimal builds (prod 1b4ea74 handlers, no minimal no-op)', () => {
    expect(read('components/media/MovieDetailModal/index.tsx')).not.toMatch(/isMinimal\(\)\) return/);
    expect(read('components/media/TvDetailModal/index.tsx')).not.toMatch(/isMinimal/);
  });
});
