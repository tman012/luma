import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTitleReveal, LOGO_WAIT_MS } from '@/hooks/useTitleReveal';

type Behavior = 'ok' | 'error' | 'never';
let behavior: Behavior = 'ok';

class FakeImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  decode = vi.fn(() => Promise.resolve());
  set src(_v: string) {
    if (behavior === 'never') return;
    setTimeout(() => (behavior === 'ok' ? this.onload?.() : this.onerror?.()), 50);
  }
}

describe('detail popup title reveal', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  const flush = async (ms: number) => { await act(async () => { vi.advanceTimersByTime(ms); await Promise.resolve(); await Promise.resolve(); }); };

  it('holds (no text) until the logo has loaded and decoded, then shows the logo', async () => {
    behavior = 'ok';
    const { result } = renderHook(() => useTitleReveal('/logo.png', 'a'));
    expect(result.current).toBe('hold');
    await flush(60);
    expect(result.current).toBe('logo');
  });

  it('falls back to the text title when the logo fails', async () => {
    behavior = 'error';
    const { result } = renderHook(() => useTitleReveal('/logo.png', 'a'));
    expect(result.current).toBe('hold');
    await flush(60);
    expect(result.current).toBe('text');
  });

  it('falls back to text after the wait when the logo never loads', async () => {
    behavior = 'never';
    const { result } = renderHook(() => useTitleReveal('/logo.png', 'a'));
    await flush(LOGO_WAIT_MS - 100);
    expect(result.current).toBe('hold');
    await flush(200);
    expect(result.current).toBe('text');
  });

  it('with no logo it holds for the wait (a logo URL may still arrive), then shows text', async () => {
    behavior = 'ok';
    const { result, rerender } = renderHook(({ url }) => useTitleReveal(url, 'a'), { initialProps: { url: null as string | null } });
    await flush(500);
    expect(result.current).toBe('hold');
    rerender({ url: '/late-logo.png' });
    await flush(60);
    expect(result.current).toBe('logo');
    const none = renderHook(() => useTitleReveal(null, 'b'));
    await flush(LOGO_WAIT_MS + 10);
    expect(none.result.current).toBe('text');
  });

  it('never jumps from the text title to a late logo', async () => {
    behavior = 'ok';
    const { result, rerender } = renderHook(({ url }) => useTitleReveal(url, 'a'), { initialProps: { url: null as string | null } });
    await flush(LOGO_WAIT_MS + 10);
    expect(result.current).toBe('text');
    rerender({ url: '/too-late.png' });
    await flush(100);
    expect(result.current).toBe('text');
  });
});
