/** v222/v301: at phone width the very first render is the phone layout with week-strip +
 * Home-style shimmer skeletons, never the desktop week grid. */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TvCalendar } from '@/components/library/TvCalendar';

const mm = (phone: boolean) => vi.stubGlobal('matchMedia', (q: string) => ({
  matches: phone && q === '(max-width: 767px)', media: q, addEventListener: () => {}, removeEventListener: () => {},
  addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
}));
const never = () => vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('TV calendar first paint (v222)', () => {
  it('phone width: first render has no desktop grid or grid skeleton', () => {
    mm(true); never();
    render(<TvCalendar today="2026-10-21" week="2026-10-18" onWeek={() => {}} toolbar={false} />);
    expect(screen.queryByTestId('tv-cal-loading')).toBeNull();
    expect(screen.queryAllByTestId('tv-cal-loading-day')).toHaveLength(0);
    expect(screen.queryByTestId('tv-day-header')).toBeNull();
    expect(screen.getByTestId('tv-cal-loading-phone')).toBeTruthy();
    expect(screen.getByTestId('tv-cal-loading-strip')).toBeTruthy();
    expect(screen.getAllByTestId('tv-cal-loading-chip')).toHaveLength(7);
    expect(screen.getAllByTestId('tv-cal-loading-row').length).toBe(5);
  });
  it('phone sheet (forced phone): phone skeleton', () => {
    mm(false); never();
    render(<TvCalendar today="2026-10-21" week="2026-10-18" onWeek={() => {}} toolbar={false} phone />);
    expect(screen.queryByTestId('tv-cal-loading')).toBeNull();
    expect(screen.getByTestId('tv-cal-loading-phone')).toBeTruthy();
    expect(screen.getByTestId('tv-cal-loading-strip')).toBeTruthy();
  });
  it('desktop width: grid skeleton unchanged', () => {
    mm(false); never();
    render(<TvCalendar today="2026-10-21" week="2026-10-18" onWeek={() => {}} toolbar={false} />);
    expect(screen.getByTestId('tv-cal-loading')).toBeTruthy();
    expect(screen.queryByTestId('tv-cal-loading-phone')).toBeNull();
  });
});

describe('TV calendar phone loading Home-style shimmer (v301)', () => {
  it('phone loading: strip without dots + 5 skeleton-shimmer cards, no spinner', () => {
    mm(true); never();
    const { container } = render(<TvCalendar today="2026-10-21" week="2026-10-18" onWeek={() => {}} toolbar={false} />);
    const phone = screen.getByTestId('tv-cal-loading-phone');
    expect(phone.querySelectorAll('.skeleton-shimmer').length).toBeGreaterThanOrEqual(5);
    const rows = screen.getAllByTestId('tv-cal-loading-row');
    expect(rows).toHaveLength(5);
    for (const row of rows) {
      expect(row.querySelector('.skeleton-shimmer')).toBeTruthy();
      // neutral edge (SPEC / variant-B edge-none)
      expect((row as HTMLElement).style.boxShadow).toContain('inset');
    }
    // strip chips have no state dots
    for (const chip of screen.getAllByTestId('tv-cal-loading-chip')) {
      expect(chip.querySelectorAll('[data-testid="tv-stripe"], i').length).toBe(0);
    }
    expect(container.querySelector('[role="progressbar"], .spinner, .animate-spin')).toBeNull();
    expect(screen.queryByTestId('tv-cal-loading')).toBeNull();
  });
});
