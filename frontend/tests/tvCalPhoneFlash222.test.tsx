/** v222: at phone width the very first render is the phone layout with phone-shaped skeletons, never the desktop grid. */
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
    expect(screen.getByTestId('tv-cal-loading-phone')).toBeTruthy();
    expect(screen.getAllByTestId('tv-cal-loading-row').length).toBe(3);
  });
  it('phone sheet (forced phone): phone skeleton', () => {
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
