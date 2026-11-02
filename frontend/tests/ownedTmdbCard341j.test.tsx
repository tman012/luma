import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { cardIds, cardArt } from '@/components/home/Rails';
import { plexOwned } from '@/components/home/railOwnership';
import { openDetail } from '@/pages/MinimalHomePage';
import { plexArtFromPath } from '@/utils/image';

const tulsa = { key: 'tmdb-show-153312', type: 'show', title: 'Tulsa King', subtitle: '2022', tmdb_id: 153312,
  rating_key: 39580, owned: true, art_path: '/library/metadata/39580/art/1699' };

describe('v341j: an owned TMDb-keyed Home card (server-marked) opens by its Plex key on its Plex art', () => {
  it('opens with both ids (no client ownership check needed), owned orb, Plex art from the tap', () => {
    expect(cardIds(tulsa as never)).toEqual({ kind: 'tv', tmdbId: 153312, ratingKey: 39580 });
    const calls: Record<string, unknown>[] = [];
    openDetail({ openMovie: () => {}, openTv: (o) => calls.push(o as Record<string, unknown>) }, tulsa as never);
    expect(calls[0]).toMatchObject({ tmdbId: 153312, ratingKey: 39580 });
    expect(plexOwned(tulsa as never)).toEqual({ owned: true, ownerType: 'self' });
    expect(cardArt(tulsa as never)[0]).toContain('/library/metadata/39580/art/1699');
  });
  it('an unmarked TMDb card stays TMDb-keyed and unresolved', () => {
    const un = { key: 'tmdb-show-1', type: 'show', title: 'Other', subtitle: '2022', tmdb_id: 1 };
    expect(cardIds(un as never)).toEqual({ kind: 'tv', tmdbId: 1 });
    expect(plexOwned(un as never)).toBeNull();
    expect(cardArt(un as never)).toEqual([]);
  });
  it('plexArtFromPath takes only library art paths', () => {
    expect(plexArtFromPath('/library/metadata/39580/art/1699')).toContain('/library/metadata/39580/art/1699');
    for (const bad of ['/library/metadata/1/thumb', 'http://x/library/metadata/1/art', '/library/metadata/1/art?X-Plex-Token=a', null, 5])
      expect(plexArtFromPath(bad)).toBeNull();
  });
  it('a TMDb-id open the server marks owned is owned at the first answer, on Plex art, and never flips back', () => {
    const tv = readFileSync('src/hooks/useTvDetail.ts', 'utf8');
    expect(tv).toMatch(/const ownedRk = Number\(data\.plex_rating_key\) \|\| 0;/);
    expect(tv).toMatch(/showOwned: showOwned \|\| !!show\?\.isOwned,/);
    const mv = readFileSync('src/hooks/useMovieDetail.ts', 'utf8');
    expect(mv).toMatch(/parsed\.ownershipSettled = true;\s*parsed\.ratingKey = ownedRk;/);
  });
});
