import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TvSeries, episodesText, letterOf, progress, sortKey, sortShows } from '@/components/library/TvSeries';
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
    expect([letterOf('The Wire'), letterOf('24'), letterOf('¡Ay!'), letterOf('...')]).toEqual(['W', '#', 'A', '#']);   // symbols are ignored
  });

  it('punctuation and a leading The/A/An are ignored (mirrors tv_scan.sort_title)', () => {
    expect(['S.W.A.T.', "Grey's Anatomy", 'A Knight of the Seven Kingdoms', 'An Idiot Abroad', 'A.P. Bio', 'Shōgun', 'Teen Mom: Family Reunion']
      .map(sortKey)).toEqual(['swat', 'greys anatomy', 'knight of the seven kingdoms', 'idiot abroad', 'ap bio', 'shogun', 'teen mom family reunion']);
    const names = ['The Santa Clauses', 'S.W.A.T.', 'I, Sniper', 'I Will Find You', 'The Marvelous Mrs. Maisel', "Marvel's The Punisher",
      'Teen Mom OG', 'Teen Mom: Family Reunion', 'SWAT Kats', 'A Knight of the Seven Kingdoms'];
    expect(sortShows(names.map((t, i) => show(i + 1, t))).map(x => x.title)).toEqual(['I, Sniper', 'I Will Find You', 'A Knight of the Seven Kingdoms',
      'The Marvelous Mrs. Maisel', "Marvel's The Punisher", 'The Santa Clauses', 'S.W.A.T.', 'SWAT Kats', 'Teen Mom: Family Reunion', 'Teen Mom OG']);
    expect(letterOf('S.W.A.T.')).toBe('S');
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
    // the rail: 32px wide, letters 13/600 spread evenly over the full available height, 24px targets, hover pill
    expect([rail.style.width, rail.style.justifyContent, rail.style.flexDirection]).toEqual(['32px', 'space-between', 'column']);
    expect(parseInt(rail.style.height, 10)).toBeGreaterThanOrEqual(27 * 24);
    const w = screen.getByTestId('tv-rail-W');
    expect([w.style.width, w.style.height, w.style.fontSize, w.style.fontWeight]).toEqual(['24px', '24px', '13px', '600']);
    expect(w.className).toContain('lp-focus');
    expect(w.className).toContain('tvs-rail-btn');
    expect(document.head.textContent + document.body.innerHTML).toContain('.tvs-rail-btn[aria-disabled=false]:hover');
    expect(rail.getAttribute('data-pin')).toBe('top');                              // pinned (fixed) only once scrolled past
    // captions: 13/600, 17px lines, 2 lines max with an ellipsis, always 34px tall; the full name in title + aria-label
    const name = within(tiles[2]!).getByTestId('tv-series-name');
    expect([name.style.fontSize, name.style.fontWeight, name.style.lineHeight, name.style.minHeight, name.style.webkitLineClamp, name.style.display])
      .toEqual(['13px', '600', '17px', '', '2', '-webkit-box']);
    const cap = within(tiles[2]!).getByTestId('tv-series-caption');                 // the count right under the name; spare room at the bottom
    expect([cap.style.minHeight, cap.style.paddingTop, cap.style.justifyContent]).toEqual(['59px', '8px', 'flex-start']);   // 51 + 8
    expect(name.nextElementSibling!.getAttribute('data-testid')).toBe('tv-series-count');
    expect([name.getAttribute('title'), name.getAttribute('aria-label')]).toEqual(['The Wire', 'The Wire']);
    const count = within(tiles[2]!).getByTestId('tv-series-count');
    expect([count.style.whiteSpace, count.style.textOverflow]).toEqual(['nowrap', 'ellipsis']);
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

describe('Series rail pin', () => {
  it('pins in the scroll event that crosses the edge (no rAF wait), and a re-render keeps it', () => {
    const raf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1);   // rAF never runs
    let top = 100;
    const rect = (t: number, h: number) => ({ top: t, bottom: t + h, left: 0, right: 0, width: 0, height: h, x: 0, y: t, toJSON: () => ({}) });
    const real = Element.prototype.getBoundingClientRect;
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      return (this as HTMLElement).dataset.testid === 'tv-series' ? rect(top, 3000) as DOMRect : real.call(this);
    });
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(540);
    const { rerender } = render(<TvSeries shows={[show(1, 'Andor'), show(2, 'Wire')]} />);
    const rail = screen.getByTestId('tv-series-rail');
    expect(rail.getAttribute('data-pin')).toBe('top');
    top = -58;
    fireEvent.scroll(window);
    expect([rail.getAttribute('data-pin'), rail.style.position, rail.style.top]).toEqual(['fixed', 'fixed', '24px']);   // same event, no frame later
    rerender(<TvSeries shows={[show(1, 'Andor'), show(2, 'Wire')]} />);
    expect(rail.style.position).toBe('fixed');
    top = 100;
    fireEvent.scroll(window);
    expect([rail.getAttribute('data-pin'), rail.style.position, rail.style.top]).toEqual(['top', 'absolute', '0px']);
    expect(raf).toHaveBeenCalled();                                                 // the letter still waits for a frame
  });
});
