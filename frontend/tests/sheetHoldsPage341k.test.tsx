import { describe, it, expect, beforeEach } from 'vitest';
import { beginSheetPriority, endSheetPriority, _resetSheetPriority } from '@/loading/sheetPriority';
import { resultArt, resultIds } from '@/services/searchApi';

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('v341k: a tap holds every page image until the reveal (rail posters too, and ones that start after the tap)', () => {
  beforeEach(() => { _resetSheetPriority(); document.body.innerHTML = ''; });
  it('holds on-screen and later-mounted posters, never the sheet, and restores them at the reveal', async () => {
    document.body.innerHTML = '<img id="a" src="/p1.jpg" srcset="/p1.jpg 1x"><div data-modal="TvDetailModal"><img id="s" src="/art.jpg"></div>';
    beginSheetPriority();
    const a = document.getElementById('a')!;
    expect(a.getAttribute('src')).toBeNull();
    expect(document.getElementById('s')!.getAttribute('src')).toBe('/art.jpg');
    const late = document.createElement('img'); late.setAttribute('src', '/p2.jpg'); document.body.appendChild(late);
    await flush();
    expect(late.getAttribute('src')).toBeNull();                       // started after the tap: held too
    endSheetPriority();
    expect(a.getAttribute('src')).toBe('/p1.jpg');
    expect(a.getAttribute('srcset')).toBe('/p1.jpg 1x');
    expect(late.getAttribute('src')).toBe('/p2.jpg');
    const after = document.createElement('img'); after.setAttribute('src', '/p3.jpg'); document.body.appendChild(after);
    await flush();
    expect(after.getAttribute('src')).toBe('/p3.jpg');                 // after the reveal nothing is held
  });
});

describe('v341k: an owned search row opens like its Home card (both ids, its Plex art from the tap)', () => {
  it('uses the server art_path, never the TMDb backdrop', () => {
    const row = { id: 92145, rating_key: 92145, tmdb_id: 10061, isOwned: true, media_type: 'movie', title: 'Escape from L.A.',
      art_path: '/library/metadata/92145/art/1793430969', backdrop_path: '/tmdb.jpg' };
    expect(resultIds(row as never)).toEqual({ kind: 'movie', ratingKey: 92145, tmdbId: 10061 });
    const art = resultArt(row as never);
    expect(art).toHaveLength(1);
    expect(art[0]).toContain('/library/metadata/92145/art/1793430969');
    expect(art[0]).not.toContain('tmdb');
    expect(resultArt({ ...row, art_path: '/library/metadata/1/thumb', backdrop_url: null } as never)[0]).toContain('tmdb');
  });
});
