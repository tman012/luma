import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { resultIds, openIds } from '@/services/searchApi';
import { openDetail } from '@/pages/MinimalHomePage';
import { BackdropHero } from '@/components/media/BackdropHero';
import type { BackdropEffects } from '@/hooks/useBackdropEffects';
import type { SearchMovie } from '@/services/searchApi';

const src = (p: string) => readFileSync(resolve(__dirname, '../src', p), 'utf8');
const fx = { headerImgStyle: {}, headerContainerMask: {} } as unknown as BackdropEffects;
afterEach(() => { cleanup(); });

function homeCall(card: Record<string, unknown>) {
  const calls: { kind: string; o: Record<string, unknown> }[] = [];
  openDetail({ openMovie: (o) => calls.push({ kind: 'movie', o: o as Record<string, unknown> }), openTv: (o) => calls.push({ kind: 'tv', o: o as Record<string, unknown> }) }, card as never);
  return calls[0]!;
}

describe('v340: search opens a title exactly like Home', () => {
  it('same ids as Home: a TMDb id opens by that id alone, else the Plex key (movie and TV, owned and not)', () => {
    const cases: [SearchMovie, Record<string, unknown>][] = [
      [{ media_type: 'movie', isOwned: true, rating_key: 5003, tmdb_id: 329865, title: 'Arrival' } as unknown as SearchMovie, { type: 'movie', key: '5003', tmdb_id: 329865 }],
      [{ media_type: 'tv', isOwned: true, rating_key: 7001, tmdb_id: 95396, name: 'Severance' } as unknown as SearchMovie, { type: 'show', key: '7001', tmdb_id: 95396 }],
      [{ media_type: 'movie', isOwned: true, rating_key: 5004, title: 'Arrival Day' } as unknown as SearchMovie, { type: 'movie', key: '5004' }],
      [{ media_type: 'tv', id: 1396, name: 'Breaking Bad' } as unknown as SearchMovie, { type: 'show', key: 'x', tmdb_id: 1396 }],
    ];
    for (const [item, card] of cases) {
      const h = homeCall(card);
      const s = resultIds(item);
      expect(s.kind).toBe(h.kind);
      expect({ ratingKey: s.ratingKey, tmdbId: s.tmdbId }).toEqual({ ratingKey: h.o.ratingKey, tmdbId: h.o.tmdbId });
    }
    expect(openIds(undefined, 9)).toEqual({ ratingKey: 9, tmdbId: undefined });
  });

  it('every search surface takes its ids from resultIds/openIds (no ratingKey + tmdbId pair)', () => {
    for (const f of ['components/search/HomeSearchPalette.tsx', 'components/search/HomeSearchR3.tsx', 'components/search/KioskSearch.tsx',
      'components/search/SearchShelf.tsx', 'components/mobile/MobileSearchOverlay.tsx']) {
      const c = src(f);
      expect(c, f).toMatch(/resultIds\(item\)/);
      expect(c, f).not.toMatch(/isOwned && (rk|item\.rating_key|credit\.rating_key)\) \?/);
    }
  });

  it('one backdrop src per hero, faded in once when it paints (v341: no seed handoff left, the hero mounts at the reveal)', () => {
    const a = render(<BackdropHero backdropUrl="/bd.jpg" effects={fx} phase="visible" instant />);
    const img = a.container.querySelector('img')!;
    expect(img.style.opacity).toBe('0');
    expect(img.style.transition).toMatch(/opacity 0\.45s/);
    fireEvent.load(img);
    expect(img.style.opacity).toBe('1');
    expect(a.container.querySelectorAll('img')).toHaveLength(1);
    expect(src('components/media/BackdropHero.tsx')).not.toMatch(/PAINTED|handoff/);
  });


});
