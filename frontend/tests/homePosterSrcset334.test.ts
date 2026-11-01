import { describe, it, expect } from 'vitest';
import { cardSrcSet, CARD_SIZES } from '@/components/home/homeApi';

describe('Home card srcset', () => {
  it('TMDb proxy poster: w342 covers the 160px box at 2x', () => {
    expect(cardSrcSet('/api/img/tmdb?path=%2Fp.jpg&size=w342')).toBe('/api/img/tmdb?path=%2Fp.jpg&size=w342 342w');
  });
  it('TMDb CDN wide art', () => {
    expect(cardSrcSet('https://image.tmdb.org/t/p/w300/p.jpg', 'wide')).toContain('https://image.tmdb.org/t/p/w780/p.jpg 780w');
  });
  it('Plex poster and wide art', () => {
    expect(cardSrcSet('/api/img/plex?url=%2Flibrary%2F1&w=300')).toBe(
      '/api/img/plex?url=%2Flibrary%2F1&w=300 300w, /api/img/plex?url=%2Flibrary%2F1&w=500 500w');
    expect(cardSrcSet('/api/img/plex?url=%2Fa&w=480', 'wide')).toBe('/api/img/plex?url=%2Fa&w=480 480w, /api/img/plex?url=%2Fa&w=960 960w');
  });
  it('unknown or unsized URLs: no srcset', () => {
    expect(cardSrcSet('/api/img/tmdb?p=1.png')).toBeUndefined();
    expect(cardSrcSet(null)).toBeUndefined();
  });
  it('sizes match the card boxes', () => {
    expect(CARD_SIZES.poster).toBe('clamp(112px, 30vw, 160px)');
    expect(CARD_SIZES.watchlist).toBe('116px');
  });
});
