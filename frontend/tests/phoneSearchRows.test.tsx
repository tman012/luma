import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/contexts/ModalHostContext', () => ({
  useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), close: vi.fn() }),
}));

import { MobileSearchOverlay } from '@/components/mobile/MobileSearchOverlay';

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });

describe('phone search rows (Designer spec)', () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = undefined; });

  it('72px rows: 40x60 thumbs (icon placeholder when missing), title, "year · type" meta, person circle, inset dividers', async () => {
    vi.stubGlobal('fetch', vi.fn(async (u: string) => String(u).includes('/api/library/search')
      ? json({ movies: [], tv_shows: [], people: [] })
      : json({ movies: [{ id: 1, title: 'Interstellar', release_date: '2014-11-05', poster_path: '/i.jpg', media_type: 'movie', relevance_rank: 0 }],
               tv_shows: [{ id: 2, name: 'Breaking Bad', first_air_date: '2008-01-20', poster_path: null, media_type: 'tv', relevance_rank: 1 }],
               people: [{ id: 3, name: 'Matthew McConaughey', profile_path: '/m.jpg', popularity: 9 }] })));
    (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true; // posters load through the proxy
    render(<MobileSearchOverlay initialQuery="inter" onClose={() => {}} />);
    const rows = await screen.findAllByTestId('phone-search-row', {}, { timeout: 3000 });
    expect(rows.length).toBe(3);
    for (const r of rows) expect(r.className).toContain('h-[72px]');
    const metas = screen.getAllByTestId('row-meta').map((m) => m.textContent);
    expect(metas).toContain('2014 · Movie');
    expect(metas).toContain('2008 · TV show');
    const thumbs = screen.getAllByTestId('row-thumb');
    const tv = rows.find((r) => r.textContent?.includes('Breaking Bad'))!;
    expect(tv.querySelector('[data-testid="row-icon-tv"]')).toBeTruthy();
    expect(tv.querySelector('img')).toBeNull();
    const movie = rows.find((r) => r.textContent?.includes('Interstellar'))!;
    const img = movie.querySelector('img')!;
    expect(img.getAttribute('src')).toContain('/api/img/tmdb');
    expect(movie.querySelector('[data-testid="row-icon-movie"]')).toBeTruthy(); // until it loads
    // (A late re-render can remount the thumb, so load whatever img is current.)
    await waitFor(() => {
      const cur = screen.getAllByTestId('phone-search-row').find((r) => r.textContent?.includes('Interstellar'))!;
      fireEvent.load(cur.querySelector('img')!);
      expect(cur.querySelector('[data-testid="row-icon-movie"]')).toBeNull();
    });
    const person = rows.find((r) => r.textContent?.includes('McConaughey'))!;
    expect(person.querySelector('[data-testid="row-thumb"]')!.className).toContain('rounded-full');
    expect(thumbs.some((t) => t.className.includes('h-[60px] w-[40px]'))).toBe(true);
    expect(screen.getAllByTestId('row-divider').length).toBe(2);
    expect(screen.getAllByTestId('row-divider')[0]!.className).toContain('left-[68px]');
  });
});
