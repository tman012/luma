/** v322 (Taylor's pick, option C minimal glass): the phone TV Calendar day view. Same show at the same time merges
 * into one card with an inner list; the art is the banner, else the backdrop, else plain glass; a large light
 * time with a small AM/PM; no title text, no counts. Desktop WeekGrid untouched. */
import { afterEach, describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cleanup, render, screen, within } from '@testing-library/react';
import { GLASS, GLASS_DOT, PhoneDay, etParts, glassArt, glassGroups, glassState, groupState } from '@/components/library/TvCalendar';
import type { CalEpisode, Day } from '@/components/library/tvCalendarApi';
afterEach(cleanup);

const ep = (o: Partial<CalEpisode>): CalEpisode => ({ episode_id: 1, series_id: 1, show: 'Avatar: Seven Havens', year: 2026, poster_path: null, season: 1, episode: 1,
  title: 'The Spirit Gate', air_date: '2026-10-30', downloaded: false, quality: null, monitored: true, airstamp: '2026-10-30T07:00:00Z', ...o });
const DAY = '2026-10-30' as Day;
const AVATAR = [1, 2, 3, 4].map(n => ep({ episode_id: n, episode: n, title: `Part ${n}` }));

describe('v322 glass grouping', () => {
  it('merges same series + same airstamp; a different time or show is its own card; order kept, episodes in order', () => {
    const g = glassGroups([
      AVATAR[2]!, AVATAR[0]!, ep({ episode_id: 9, series_id: 2, show: 'VisionQuest' }), AVATAR[1]!, AVATAR[3]!,
      ep({ episode_id: 10, episode: 5, airstamp: '2026-10-30T22:00:00Z' }),
    ]);
    expect(g.map(x => x.eps.map(e => e.episode_id))).toEqual([[1, 2, 3, 4], [9], [10]]);
  });
  it('untimed episodes of one show on the day share a card', () => {
    expect(glassGroups([ep({ episode_id: 1, airstamp: null }), ep({ episode_id: 2, episode: 2, airstamp: null })])).toHaveLength(1);
  });
  it('a merged card reads missing if any is missing, have it only if all are', () => {
    const today = '2026-11-02' as Day;
    expect(groupState([ep({ downloaded: true }), ep({ downloaded: true })], today)).toBe('downloaded');
    expect(groupState([ep({ downloaded: true }), ep({ downloaded: false })], today)).toBe('missing');
  });
  it('renders one card listing every episode of the merged show (r2-A: rows on the glass)', () => {
    render(<PhoneDay day={DAY} today={DAY} eps={[...AVATAR, ep({ episode_id: 9, series_id: 2, show: 'VisionQuest', episode: 5, title: 'Echo Chamber' })]} />);
    const cards = screen.getAllByTestId('tv-agenda-row');
    expect(cards).toHaveLength(2);
    expect(cards[0]!.dataset.merged).toBe('true');
    expect(within(cards[0]!).getAllByTestId('tv-glass-ep').map(r => r.textContent)).toEqual(
      ['S01E01\u00b7 Part 1', 'S01E02\u00b7 Part 2', 'S01E03\u00b7 Part 3', 'S01E04\u00b7 Part 4']);
    expect(within(cards[0]!).getAllByTestId('tv-card-show')).toHaveLength(1);
    expect(cards[1]!.dataset.merged).toBe('false');
    expect(within(cards[1]!).getAllByTestId('tv-glass-ep').map(r => r.textContent)).toEqual(['S01E05\u00b7 Echo Chamber']);
    expect(within(cards[1]!).getAllByTestId('tv-glass-list')).toHaveLength(1);   // r2-A: same block for one or many
  });
});

describe('v322 glass art fallback', () => {
  const BANNER = 'https://artworks.thetvdb.com/banners/graphical/1-g.jpg';
  it('banner first (through the TheTVDB proxy), else the backdrop, else none', () => {
    expect(glassArt({ banner_url: BANNER, backdrop_path: '/b.jpg' })).toContain('host=tvdb');
    expect(glassArt({ banner_url: null, backdrop_path: '/b.jpg' })).toMatch(/\/b\.jpg/);
    expect(glassArt({ banner_url: null, backdrop_path: null })).toBeNull();
    expect(glassArt({})).toBeNull();
  });
  it('a card with art paints a faint blurred layer; without art it is the plain card gradient', () => {
    render(<PhoneDay day={DAY} today={DAY} eps={[ep({ banner_url: BANNER }), ep({ episode_id: 2, series_id: 2, show: 'Bluey' })]} />);
    const [withArt, plain] = screen.getAllByTestId('tv-agenda-row');
    expect(withArt!.dataset.art).toBe('true');
    const art = within(withArt!).getByTestId('tv-glass-art');
    expect(art.style.filter).toBe(`blur(${GLASS.glowBlur}px)`);
    expect(art.getAttribute('alt')).toBe('');
    expect(art.style.opacity).toBe('0');                           // fades in on load
    expect(plain!.dataset.art).toBe('false');
    expect(within(plain!).queryByTestId('tv-glass-art')).toBeNull();
    expect(plain!.style.backgroundImage).toMatch(/#26282d|rgb\(38, 40, 45\)/);   // TOKENS-C card fill, nothing else
  });
});

describe('v322 time and text', () => {
  it('r2-A: the time is one small accent line (12px/600 #adc7ff), no pill', () => {
    expect(etParts('2026-10-30T07:00:00Z')).toEqual(['3:00', 'AM']);
    expect(etParts('2026-10-31T02:00:00Z')).toEqual(['10:00', 'PM']);
    render(<PhoneDay day={DAY} today={DAY} eps={[ep({ airstamp: '2026-10-31T02:00:00Z' })]} />);
    const t = screen.getByTestId('tv-glass-time');
    expect(t.style.fontSize).toBe('12px'); expect(t.style.fontWeight).toBe('600');
    expect(t.style.color).toMatch(/#adc7ff|rgb\(173, 199, 255\)/);
    expect(t.textContent).toBe('10:00 PM');
    expect(t.style.background).toBe(''); expect(t.style.borderRadius).toBe(''); expect(t.style.padding).toBe('');
    expect(screen.queryByTestId('tv-glass-ampm')).toBeNull();
  });
  it('no title text, no counts, no helper text', () => {
    const { container } = render(<PhoneDay day={DAY} today={DAY} eps={[...AVATAR, ep({ episode_id: 9, series_id: 2, show: 'VisionQuest' })]} />);
    const txt = container.textContent || '';
    expect(txt).not.toMatch(/\b\d+\s+(episodes?|shows?)\b/i);
    expect(txt).not.toMatch(/TV calendar|Calendar|Tonight|Today/i);
    expect(container.querySelector('h1,h2,h3,h4')).toBeNull();
    expect(container.querySelector('[data-testid="tv-day-count"]')).toBeNull();
  });
  it('desktop untouched: the week grid card and Placeholder source are not glass', () => {
    const cal = readFileSync(resolve(__dirname, '../src/components/library/TvCalendar.tsx'), 'utf8');
    const grid = cal.slice(cal.indexOf('export function WeekGrid'), cal.indexOf('export function Agenda'));
    expect(grid).not.toMatch(/Glass|glassArt|GLASS/);
  });
});

describe('v322 TOKENS-C states, motion and ellipsis', () => {
  const today = '2026-10-31' as Day;
  it('each state and its dot color and screen-reader label', () => {
    const cases: [Partial<CalEpisode>, string, string][] = [
      [{ downloaded: true }, 'downloaded', '#9ad5a0'],
      [{ air_date: '2026-10-30' }, 'missing', '#ffb870'],
      [{ air_date: '2026-11-02' }, 'upcoming', '#adc7ff'],
      [{ grab: 'downloading' }, 'downloading', '#adc7ff'],
      [{ monitored: false }, 'unmonitored', '#8e9099'],
      [{ grab: 'failed' }, 'failed', '#ffb4ab'],
    ];
    for (const [o, st, hex] of cases) {
      expect(glassState(ep(o), today)).toBe(st);
      expect(GLASS_DOT[st as keyof typeof GLASS_DOT]).toBe(hex);
    }
    render(<PhoneDay day={DAY} today={today} eps={[ep({ grab: 'downloading' })]} />);
    const dot = screen.getByTestId('tv-glass-dot');
    expect(dot.getAttribute('role')).toBe('img');
    expect(dot.getAttribute('aria-label')).toBe('Downloading');
    expect(dot.className).toContain('tvg-pulse');
    expect(dot.textContent).toBe('');
    expect(dot.style.width).toBe('9px'); expect(dot.style.height).toBe('9px');
  });
  it('a mixed group: worst state wins (missing > downloading > upcoming > downloaded)', () => {
    const g = (...o: Partial<CalEpisode>[]) => groupState(o.map((x, i) => ep({ episode_id: i + 1, ...x })), today);
    expect(g({ downloaded: true }, { grab: 'downloading' }, { air_date: '2026-10-30' })).toBe('missing');
    expect(g({ downloaded: true }, { grab: 'downloading' }, { air_date: '2026-11-02' })).toBe('downloading');
    expect(g({ downloaded: true }, { air_date: '2026-11-02' })).toBe('upcoming');
    expect(g({ downloaded: true }, { downloaded: true })).toBe('downloaded');
  });
  it('groups by the air minute, whatever the stamp format', () => {
    expect(glassGroups([ep({ airstamp: '2026-10-30T07:00:00Z' }), ep({ episode_id: 2, episode: 2, airstamp: '2026-10-30T07:00:00.000+00:00' })])).toHaveLength(1);
  });
  it('art order: banner, then poster, then backdrop', () => {
    expect(glassArt({ banner_url: null, poster_path: '/p.jpg', backdrop_path: '/b.jpg' })).toMatch(/p\.jpg/);
  });
  it('first load rises with a capped stagger; otherwise a plain crossfade', () => {
    const eps = Array.from({ length: 10 }, (_, i) => ep({ episode_id: i + 1, series_id: i + 1, show: `Show ${i}` }));
    const { unmount } = render(<PhoneDay day={DAY} today={DAY} eps={eps} motion="rise" />);
    const cards = screen.getAllByTestId('tv-agenda-row');
    expect(cards[0]!.className).toContain('tvg-rise');
    expect(cards[3]!.style.animationDelay).toContain('3 * var(--motion-stagger, 30ms)');
    expect(cards[3]!.style.animationDelay).toContain('var(--motion-stagger-max, 240ms)');
    unmount();
    render(<PhoneDay day={DAY} today={DAY} eps={eps} motion="fade" />);
    expect(screen.getByTestId('tv-cal-phone').className).toBe('tvg-fade');
    expect(screen.getAllByTestId('tv-agenda-row')[0]!.className).not.toContain('tvg-rise');
  });
  it('the episode code never truncates; only the title ellipsizes', () => {
    render(<PhoneDay day={DAY} today={DAY} eps={[ep({ title: 'A very long title that will never fit on one line of a phone card at all' })]} />);
    expect(screen.getByTestId('tv-glass-code').style.whiteSpace).toBe('nowrap');
    const t = screen.getByTestId('tv-glass-ep-title');
    expect(t.style.textOverflow).toBe('ellipsis'); expect(t.style.overflow).toBe('hidden');
  });
});
