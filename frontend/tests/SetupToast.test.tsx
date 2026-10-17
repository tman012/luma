import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  SETUP_TOAST_KEY, SetupToast, TOTAL_MS, FADE_IN_MS, HOLD_MS, FADE_OUT_MS, markSetupJustFinished,
} from '@/components/start/SetupToast';

function mockMotion(reduce: boolean) {
  window.matchMedia = vi.fn().mockImplementation((q: string) => ({
    matches: reduce && q.includes('prefers-reduced-motion'), media: q,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

describe('SetupToast', () => {
  beforeEach(() => { window.sessionStorage.clear(); vi.useFakeTimers(); mockMotion(false); });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it('timings are 200 in, 1800 hold, 300 out', () => {
    expect([FADE_IN_MS, HOLD_MS, FADE_OUT_MS, TOTAL_MS]).toEqual([200, 1800, 300, 2300]);
  });

  it('renders nothing without the flag', () => {
    render(<SetupToast />);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('shows once after setup, never blocks taps, then dismisses', () => {
    markSetupJustFinished();
    render(<SetupToast />);
    const el = screen.getByRole('status');
    expect(el.querySelector('span')?.textContent).toBe("You're all set");
    expect(el.querySelector('svg')).not.toBeNull();
    expect(el.querySelector('button')).toBeNull();
    expect(el.style.pointerEvents).toBe('none');
    expect(el.className).toContain('luma-setup-toast');
    expect(window.sessionStorage.getItem(SETUP_TOAST_KEY)).toBeNull();
    act(() => { vi.advanceTimersByTime(TOTAL_MS - 10); });
    expect(screen.queryByRole('status')).not.toBeNull();
    act(() => { vi.advanceTimersByTime(20); });
    expect(screen.queryByRole('status')).toBeNull();
    cleanup();
    render(<SetupToast />);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('reduced motion: no fade, still auto-dismisses', () => {
    mockMotion(true);
    markSetupJustFinished();
    const { container } = render(<SetupToast />);
    const el = screen.getByRole('status');
    expect(el.dataset.reducedMotion).toBe('true');
    expect(container.innerHTML).toContain('prefers-reduced-motion: reduce');
    act(() => { vi.advanceTimersByTime(FADE_IN_MS + HOLD_MS + 1); });
    expect(screen.queryByRole('status')).toBeNull();
  });
});
