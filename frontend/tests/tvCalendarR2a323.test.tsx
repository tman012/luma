/** v323 Phone TV Calendar r2-A (TOKENS-r2-a + Taylor: no pill behind the time). Per-line checks: line order
 * (time, show, episode block), every code and title in its own grid column (equal x), the block centered as a
 * unit, no time pill, no inner panel, the same block for one episode or many. */
import { afterEach, describe, it, expect } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { GLASS_CARD_MIN_H, PhoneDay } from '@/components/library/TvCalendar';
import type { CalEpisode, Day } from '@/components/library/tvCalendarApi';
afterEach(cleanup);

const ep = (o: Partial<CalEpisode>): CalEpisode => ({ episode_id: 1, series_id: 1, show: 'Avatar: Seven Havens', year: 2026, poster_path: null, season: 1, episode: 10,
  title: 'Ghosts and Spirits', air_date: '2026-10-30', downloaded: true, quality: null, monitored: true, airstamp: '2026-10-30T07:00:00Z', ...o });
const DAY = '2026-10-30' as Day;
const GROUP = [10, 11, 12, 13].map(n => ep({ episode_id: n, episode: n, title: `Title ${n}` }));
const SINGLE = ep({ episode_id: 99, series_id: 2, show: 'MobLand', season: 2, episode: 7, title: 'Ace of Spades', airstamp: '2026-10-30T07:00:00Z' });

const cards = () => screen.getAllByTestId('tv-agenda-row');
const content = (card: HTMLElement) => card.querySelector('[data-testid="tv-card-show"]')!.parentElement!;

describe('v323 r2-A card', () => {
  it('line order: time, then show, then the episode block; nothing else', () => {
    render(<PhoneDay day={DAY} today={DAY} eps={[...GROUP, SINGLE]} />);
    for (const c of cards()) {
      expect([...content(c).children].map(x => x.getAttribute('data-testid'))).toEqual(['tv-glass-time', 'tv-card-show', 'tv-glass-list']);
    }
  });
  it('no pill behind the time: plain 12px/600 accent text, 8px above the show', () => {
    render(<PhoneDay day={DAY} today={DAY} eps={[SINGLE]} />);
    const t = screen.getByTestId('tv-glass-time');
    expect(t.textContent).toBe('3:00 AM');
    expect(t.style.fontSize).toBe('12px'); expect(t.style.fontWeight).toBe('600'); expect(t.style.letterSpacing).toBe('0.3px');
    expect(t.style.background).toBe(''); expect(t.style.backgroundColor).toBe(''); expect(t.style.borderRadius).toBe('');
    expect(t.style.padding).toBe(''); expect(t.style.display).not.toBe('inline-block'); expect(t.style.border).toBe('');
    expect(t.style.marginBottom).toBe('8px');
    expect(t.children).toHaveLength(0);
  });
  it('the show is the 22px/700 hero, one line with ellipsis, 22px side padding', () => {
    render(<PhoneDay day={DAY} today={DAY} eps={[SINGLE]} />);
    const s = screen.getByTestId('tv-card-show');
    expect(s.style.fontSize).toBe('22px'); expect(s.style.fontWeight).toBe('700'); expect(s.style.lineHeight).toBe('28px');
    expect(s.style.letterSpacing).toBe('-0.2px'); expect(s.style.padding).toBe('0px 22px');
    expect(s.style.textOverflow).toBe('ellipsis'); expect(s.style.whiteSpace).toBe('nowrap');
  });
  it('no inner panel: the block has no fill, radius, padding or row dividers', () => {
    render(<PhoneDay day={DAY} today={DAY} eps={[...GROUP, SINGLE]} />);
    for (const b of screen.getAllByTestId('tv-glass-list')) {
      expect(b.style.background).toBe(''); expect(b.style.backgroundColor).toBe('');
      expect(b.style.borderRadius).toBe(''); expect(b.style.padding).toBe('');
      for (const r of within(b).getAllByTestId('tv-glass-ep')) { expect(r.style.borderTop).toBe(''); expect(r.style.background).toBe(''); }
      for (const el of b.querySelectorAll<HTMLElement>('*')) expect(el.style.borderTop).toBe('');
    }
  });
  it('equal code x and title x: one two-column grid, every row a subgrid spanning both, code first', () => {
    render(<PhoneDay day={DAY} today={DAY} eps={[...GROUP, SINGLE]} />);
    for (const b of screen.getAllByTestId('tv-glass-list')) {
      expect(b.style.display).toBe('grid');
      expect(b.style.gridTemplateColumns).toBe('auto minmax(0, auto)');
      expect(b.style.columnGap).toBe('6px');
      for (const r of within(b).getAllByTestId('tv-glass-ep')) {
        expect(r.parentElement).toBe(b);
        expect(r.style.display).toBe('grid'); expect(r.style.gridTemplateColumns).toBe('subgrid'); expect(r.style.gridColumn).toBe('1 / -1');
        expect(r.style.lineHeight).toBe('24px');
        const [c, t] = [...r.children] as HTMLElement[];
        expect(c!.dataset.testid).toBe('tv-glass-code'); expect(t!.dataset.testid).toBe('tv-glass-ep-title');
        expect(r.children).toHaveLength(2);
        expect(c!.style.whiteSpace).toBe('nowrap'); expect(c!.style.fontVariantNumeric).toBe('tabular-nums');
        expect(c!.style.fontWeight).toBe('600'); expect(c!.style.textOverflow).toBe('');
        expect(t!.style.textOverflow).toBe('ellipsis'); expect(t!.style.minWidth).toBe('0px');
      }
    }
  });
  it('the block is centered as a unit with its rows left-aligned (fit-content, auto margins)', () => {
    render(<PhoneDay day={DAY} today={DAY} eps={[...GROUP, SINGLE]} />);
    for (const b of screen.getAllByTestId('tv-glass-list')) {
      expect(b.style.width).toBe('fit-content'); expect(b.style.maxWidth).toBe('100%');
      expect(b.style.margin).toBe('10px auto 0px'); expect(b.style.textAlign).toBe('left');
    }
  });
  it('rows in episode order; titles prefixed with a muted "· "; grouped rows open their episode, a single card opens from the card', () => {
    render(<PhoneDay day={DAY} today={DAY} eps={[...GROUP, SINGLE]} />);
    const [g, s] = cards();
    expect(within(g!).getAllByTestId('tv-glass-code').map(x => x.textContent)).toEqual(['S01E10', 'S01E11', 'S01E12', 'S01E13']);
    expect(within(g!).getAllByTestId('tv-glass-ep-title').map(x => x.textContent)).toEqual(['\u00b7 Title 10', '\u00b7 Title 11', '\u00b7 Title 12', '\u00b7 Title 13']);
    const dot = within(s!).getByTestId('tv-glass-ep-title').firstElementChild as HTMLElement;
    expect(dot.textContent).toBe('\u00b7 '); expect(dot.style.color).toMatch(/#8e9099|rgb\(142, 144, 153\)/);
    expect(within(s!).getAllByTestId('tv-glass-ep')).toHaveLength(1);
  });
  it('reserved loading height matches the r2-A card (16+16+8+28+10+24+16, hairline)', () => {
    expect(GLASS_CARD_MIN_H).toBe(120);
  });
});
