/** v222: at phone width the very first render is the phone layout, never the desktop week grid.
 * v322 (TOKENS-C / SPEC-fade-in, supersedes v301 skeletons): phone loading keeps the day nav and reserves one
 * card's height with no placeholder cards: no skeleton, shimmer or spinner. */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { GLASS_CARD_MIN_H, TvCalendar } from '@/components/library/TvCalendar';

const mm = (phone: boolean) => vi.stubGlobal('matchMedia', (q: string) => ({
  matches: phone && q === '(max-width: 767px)', media: q, addEventListener: () => {}, removeEventListener: () => {},
  addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
}));
const never = () => vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('TV calendar first paint (v222)', () => {
  it('phone width: first render has no desktop grid or grid skeleton; the day nav shows', () => {
    mm(true); never();
    render(<TvCalendar today="2026-10-21" week="2026-10-18" onWeek={() => {}} toolbar={false} />);
    expect(screen.queryByTestId('tv-cal-loading')).toBeNull();
    expect(screen.queryAllByTestId('tv-cal-loading-day')).toHaveLength(0);
    expect(screen.getByTestId('tv-day-header')).toBeTruthy();
    expect(screen.getByTestId('tv-cal-loading-phone')).toBeTruthy();
  });
  it('phone sheet (forced phone): the same quiet phone loading', () => {
    mm(false); never();
    render(<TvCalendar today="2026-10-21" week="2026-10-18" onWeek={() => {}} toolbar={false} phone />);
    expect(screen.queryByTestId('tv-cal-loading')).toBeNull();
    expect(screen.getByTestId('tv-cal-loading-phone')).toBeTruthy();
  });
  it('desktop width: grid skeleton unchanged', () => {
    mm(false); never();
    render(<TvCalendar today="2026-10-21" week="2026-10-18" onWeek={() => {}} toolbar={false} />);
    expect(screen.getByTestId('tv-cal-loading')).toBeTruthy();
    expect(screen.queryByTestId('tv-cal-loading-phone')).toBeNull();
  });
});

describe('v322 phone loading (SPEC-fade-in)', () => {
  it('no placeholder cards, no shimmer, no spinner; one card of height reserved', () => {
    mm(true); never();
    const { container } = render(<TvCalendar today="2026-10-21" week="2026-10-18" onWeek={() => {}} toolbar={false} />);
    const phone = screen.getByTestId('tv-cal-loading-phone');
    expect(phone.children).toHaveLength(0);
    expect(phone.style.minHeight).toBe(`${GLASS_CARD_MIN_H}px`);
    expect(container.querySelectorAll('.skeleton-shimmer')).toHaveLength(0);
    expect(screen.queryAllByTestId('tv-cal-loading-row')).toHaveLength(0);
    expect(screen.queryAllByTestId('tv-cal-loading-chip')).toHaveLength(0);
    expect(container.querySelector('[role="progressbar"], .spinner, .animate-spin')).toBeNull();
  });
});
