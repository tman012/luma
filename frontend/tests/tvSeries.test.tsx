import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TvSeries, episodesText, letterOf, progress, sortShows } from '@/components/library/TvSeries';
import type { Show } from '@/components/library/tvLibraryApi';

const show = (id: number, title: string, o: Partial<Show> = {}): Show => ({
  id, tmdb_id: id, title, year: 2020, path_id: 'p', path_name: 'TV', folder: title, poster_path: `/p${id}.jpg`, monitored: false,
  files: 0, episodes: 0, linked: 0, mismatch: 0, unrecognized: 0, aired: 30, have: 24, continuing: false, ...o,
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('Series grid', () => {
  it('A–Z ignoring a leading The; # for digits and symbols', () => {
    const s = sortShows([show(1, 'The Wire'), show(2, 'Andor'), show(3, 'the Bear'), show(4, '24'), show(5, 'Severance')]);
    expect(s.map(x => x.title)).toEqual(['24', 'Andor', 'the Bear', 'Severance', 'The Wire']);
    expect([letterOf('The Wire'), letterOf('24'), letterOf('¡Ay!')]).toEqual(['W', '#', '#']);
  });

  it('the bar: red with aired missing, blue continuing with none missing, green complete; N of M episodes', () => {
    expect(progress(show(1, 'A', { aired: 30, have: 24 }))).toEqual({ fill: 0.8, color: '#ef4444', state: 'missing' });
    expect(progress(show(1, 'A', { aired: 30, have: 30, continuing: true })).color).toBe('#3b82f6');
    expect(progress(show(1, 'A', { aired: 30, have: 30 })).color).toBe('#22c55e');
    expect(progress(show(1, 'A', { aired: 0, have: 0 })).fill).toBe(0);
    expect(episodesText(show(1, 'A'))).toBe('24 of 30 episodes');
  });

  it('2:3 posters in an auto-fill grid; the rail: empty letters inert, a click scrolls to the first show', () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    render(<TvSeries shows={[show(1, 'The Wire'), show(2, 'Andor', { poster_path: null }), show(3, 'Arcane')]} />);
    const tiles = screen.getAllByTestId('tv-series-tile');
    expect(tiles.map(t => t.textContent)).toEqual(['Andor24 of 30 episodes', 'Arcane24 of 30 episodes', 'The Wire24 of 30 episodes']);
    expect(tiles[0]!.querySelector('[data-testid="tv-series-tile-tmdb"]')).toBeTruthy();         // no poster: the TMDB tile
    const grid = tiles[0]!.parentElement!;
    expect([grid.style.gridTemplateColumns, grid.style.gap]).toEqual(['repeat(auto-fill, minmax(128px, 1fr))', '16px']);
    const poster = tiles[1]!.firstElementChild as HTMLElement;
    expect([poster.style.aspectRatio, poster.style.borderRadius]).toEqual(['2 / 3', '8px 8px 0 0']);
    const bar = tiles[1]!.querySelector('[data-testid="tv-series-bar"]') as HTMLElement;
    expect([bar.style.height, bar.style.background]).toEqual(['4px', 'rgba(255, 255, 255, 0.08)']);
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('80%');
    const rail = screen.getByTestId('tv-series-rail');
    expect(rail.style.width).toBe('28px');
    expect(rail.getAttribute('data-pin')).toBe('top');                              // pinned (fixed) only once scrolled past                                          // sticky by hand (translateY on scroll)
    expect(screen.getByTestId('tv-rail-B').style.color).toBe('rgba(255, 255, 255, 0.2)');
    expect(screen.getByTestId('tv-rail-B').getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(screen.getByTestId('tv-rail-B'));
    expect(scroll).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('tv-rail-W'));
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('tv-rail-W').style.color).toBe('rgb(59, 130, 246)');
    expect(tiles.every(t => !t.querySelector('a, button') && t.tagName === 'DIV')).toBe(true);    // not clickable
  });

  it('a poster that fails to load becomes the TMDB tile', () => {
    render(<TvSeries shows={[show(1, 'Andor')]} />);
    const img = screen.getByAltText('Andor');
    expect(img.getAttribute('loading')).toBe('lazy');
    fireEvent.error(img);
    expect(screen.queryByAltText('Andor')).toBeNull();
    expect(screen.getByTestId('tv-series-tile-tmdb')).toBeTruthy();
  });

  it('loading: 2:3 glass .04 tiles; empty: No shows', () => {
    render(<TvSeries shows={null} />);
    const loading = screen.getByTestId('tv-series-loading');
    expect(loading.children).toHaveLength(12);
    expect((loading.firstElementChild as HTMLElement).style.aspectRatio).toBe('2 / 3');
    cleanup();
    render(<TvSeries shows={[]} />);
    expect(screen.getByTestId('tv-series-empty').textContent).toBe('No shows');
  });
});
