import { describe, it, expect, afterEach } from 'vitest';
import { tmdbImg, posterUrl } from '@/utils/image';

describe('TMDb images in minimal mode', () => {
  afterEach(() => { delete (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__; });

  it('go through /api/img/tmdb in minimal mode (prod proxy), and straight to the CDN otherwise', () => {
    expect(posterUrl('/abc.jpg', 'w342')).toBe('https://image.tmdb.org/t/p/w342/abc.jpg');
    window.__LUMA_MINIMAL__ = true;
    expect(posterUrl('/abc.jpg', 'w342')).toBe('/api/img/tmdb?path=%2Fabc.jpg&size=w342');
    expect(tmdbImg('https://image.tmdb.org/t/p/original/logo.png')).toBe('/api/img/tmdb?path=%2Flogo.png&size=original');
  });

  it('leave Plex proxy and other URLs alone', () => {
    window.__LUMA_MINIMAL__ = true;
    expect(tmdbImg('/api/img/plex?url=/library/metadata/1/thumb/2')).toBe('/api/img/plex?url=/library/metadata/1/thumb/2');
    expect(tmdbImg('https://example.com/x.jpg')).toBe('https://example.com/x.jpg');
    expect(tmdbImg(null)).toBeNull();
  });
});
