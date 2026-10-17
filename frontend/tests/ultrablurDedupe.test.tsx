import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { modalColors, prefetchModalColors, MISS_TTL_MS, _resetModalColors } from '@/services/modalColors';
import { useModalColors } from '@/hooks/useModalColors';
import { warmDetail, _resetModalPrefetch } from '@/services/modalPrefetch';

const EMPTY = { topLeft: null, topRight: null, bottomRight: null, bottomLeft: null };
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
const ultra = (f: ReturnType<typeof vi.fn>) => f.mock.calls.filter((c) => String(c[0]).includes('/api/plex/ultrablur/')).length;

describe('/api/plex/ultrablur is asked once per Plex open', () => {
  beforeEach(() => { _resetModalColors(); _resetModalPrefetch(); });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it('an empty palette is fetched once across prefetch, warm-up, the popup hook and later calls', async () => {
    const f = vi.fn(async (u: string) => (String(u).includes('ultrablur') ? json(EMPTY) : json({})));
    vi.stubGlobal('fetch', f);
    prefetchModalColors('movie', 41);
    await warmDetail('movie', 41);
    const { result } = renderHook(() => useModalColors('movie', 41, undefined));
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(await modalColors('movie', 41)).toBeNull();
    expect(ultra(f)).toBe(1);
  });

  it('an HTTP error is also kept briefly, then asked again after the TTL', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    const f = vi.fn(async () => json({ error: 'x' }, 404));
    vi.stubGlobal('fetch', f);
    await modalColors('movie', 7);
    await modalColors('movie', 7);
    expect(ultra(f)).toBe(1);
    vi.setSystemTime(Date.now() + MISS_TTL_MS + 1);
    await modalColors('movie', 7);
    expect(ultra(f)).toBe(2);
  });

  it('a network failure is not kept (the next call retries)', async () => {
    const f = vi.fn(async () => { throw new TypeError('offline'); });
    vi.stubGlobal('fetch', f);
    await modalColors('movie', 9);
    await modalColors('movie', 9);
    expect(ultra(f)).toBe(2);
  });

  it('a real palette is fetched once and reused', async () => {
    const f = vi.fn(async () => json({ topLeft: '112233', topRight: '445566', bottomRight: '778899', bottomLeft: 'aabbcc' }));
    vi.stubGlobal('fetch', f);
    const a = await modalColors('movie', 3);
    const b = await modalColors('movie', 3);
    expect(a).toEqual(b);
    expect(ultra(f)).toBe(1);
  });
});
