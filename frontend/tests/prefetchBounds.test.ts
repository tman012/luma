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

  it('a reset while jobs run frees all slots; the old jobs finishing later do not skew the count', async () => {
    _resetModalPrefetch();
    const release: (() => void)[] = [];
    const slow = () => new Promise<void>((r) => release.push(r));
    for (let i = 0; i < 4; i++) scheduleWarm(`old${i}`, slow);
    expect(release.length).toBe(4);
    _resetModalPrefetch();
    const fresh = vi.fn(() => new Promise<void>(() => {}));
    for (let i = 0; i < 4; i++) scheduleWarm(`new${i}`, fresh);
    expect(fresh).toHaveBeenCalledTimes(4);
    // The old jobs end now; they must not free slots the new ones hold.
    release.forEach((r) => r());
    await new Promise((r) => setTimeout(r, 0));
    const fifth = vi.fn(() => Promise.resolve());
    scheduleWarm('new4', fifth);
    expect(fifth).not.toHaveBeenCalled();
  });
});
