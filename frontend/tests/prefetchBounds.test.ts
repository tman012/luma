import { afterEach, describe, expect, it, vi } from 'vitest';
import { _resetModalPrefetch, openWhenReady, scheduleWarm, STARTED_MAX } from '@/services/modalPrefetch';

describe('prefetch bookkeeping stays bounded', () => {
  afterEach(() => { vi.useRealTimers(); _resetModalPrefetch(); });

  it('forgets the oldest warm-ups past STARTED_MAX', async () => {
    _resetModalPrefetch();
    for (let i = 0; i < STARTED_MAX + 10; i++) scheduleWarm(`k${i}`, () => Promise.resolve());
    await new Promise((r) => setTimeout(r, 0));
    // k0 was forgotten, so it can be warmed again; a recent one can't.
    const run = vi.fn(() => Promise.resolve());
    scheduleWarm('k0', run);
    scheduleWarm(`k${STARTED_MAX + 5}`, run);
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('the pressed-state safety timer is cleared once the sheet shows', async () => {
    vi.useFakeTimers();
    _resetModalPrefetch();
    const clear = vi.spyOn(window, 'clearTimeout');
    const card = document.createElement('div');
    const p = openWhenReady('movie', undefined, undefined, card, () => {});
    await vi.advanceTimersByTimeAsync(700);
    await p;
    expect(card.getAttribute('data-pressed')).toBe('true');
    const before = clear.mock.calls.length;
    window.dispatchEvent(new Event('luma:detail-revealed'));
    expect(card.hasAttribute('data-pressed')).toBe(false);
    expect(clear.mock.calls.length).toBeGreaterThan(before);
    expect(vi.getTimerCount()).toBe(0);
  });
});
