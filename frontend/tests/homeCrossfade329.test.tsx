import { describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import { SkeletonCrossfade } from '@/components/home/Rails';

function pressCard() {
  const el = document.createElement('div'); el.className = 'luma-pressable'; document.body.appendChild(el);
  el.dispatchEvent(new Event('pointerdown', { bubbles: true }));
  return el;
}

describe('v329 Home skeleton crossfade', () => {
  it('the fading copy has no shimmer; with a tap pending it holds until the press releases', async () => {
    vi.useFakeTimers();
    const { rerender, container } = render(<SkeletonCrossfade loading><span /></SkeletonCrossfade>);
    const el = pressCard();
    expect(el.getAttribute('data-pressed')).toBe('true');
    rerender(<SkeletonCrossfade loading={false}><div className="luma-rails-in" /></SkeletonCrossfade>);
    const ghost = container.querySelector('[data-testid="home-skeleton-out"]')!;
    expect(ghost).toBeTruthy();
    expect(ghost.querySelector('.luma-shimmer')).toBeNull();
    expect(container.firstElementChild!.hasAttribute('data-xf-hold')).toBe(true);
    act(() => { vi.advanceTimersByTime(600); });
    expect(container.querySelector('[data-testid="home-skeleton-out"]')).toBeTruthy();   // still held
    act(() => { window.dispatchEvent(new Event('luma:sheet-moving')); });
    expect(container.firstElementChild!.hasAttribute('data-xf-hold')).toBe(false);
    act(() => { vi.advanceTimersByTime(250); });
    expect(container.querySelector('[data-testid="home-skeleton-out"]')).toBeNull();
    el.remove(); vi.useRealTimers();
  });
});
