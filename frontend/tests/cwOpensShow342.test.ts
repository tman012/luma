import { describe, it, expect, vi } from 'vitest';
import { openDetail } from '@/pages/MinimalHomePage';
describe('Continue Watching opens the same sheet as the other rails', () => {
  it('an episode opens its show (no episode-intent context), a movie opens by its Plex key', () => {
    const openTv = vi.fn(), openMovie = vi.fn();
    openDetail({ openTv, openMovie }, { key: '501', show_key: '77', type: 'episode', season: 2, episode: 3, title: 'S' } as never);
    expect(openTv).toHaveBeenCalledTimes(1);
    expect(openTv.mock.calls[0][0]).toMatchObject({ ratingKey: 77 });
    expect(openTv.mock.calls[0][0]).not.toHaveProperty('episodeContext');
    openDetail({ openTv, openMovie }, { key: '900', type: 'movie', title: 'M' } as never);
    expect(openMovie.mock.calls[0][0]).toMatchObject({ ratingKey: 900 });
  });
});

import { readFileSync } from 'node:fs';
describe('Continue Watching uses the shared open path and the sheet with a logo', () => {
  const rails = readFileSync('src/components/home/Rails.tsx', 'utf8');
  const tvx = readFileSync('src/components/media/TvDetailModal/ExpandedLayout.tsx', 'utf8');
  const tvc = readFileSync('src/components/media/TvDetailModal/CompactLayout.tsx', 'utf8');
  it('the CW rail gets the same onOpen as the other rails (openWhenReady via openCard)', () => {
    expect(rails).toMatch(/testId="rail-continue"[\s\S]{0,200}<WideCard key=\{c\.key\} card=\{c\} onOpen=\{onOpen\}/);
  });
  it('the show sheet renders data-modal-logo (desktop) and the hero logo (phone)', () => {
    expect(tvx).toMatch(/data-modal-logo=""/);
    expect(tvc).toMatch(/data-hero-logo=""/);
  });
});
