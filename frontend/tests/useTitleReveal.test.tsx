import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useTitleReveal, LOGO_WAIT_MS } from '@/hooks/useTitleReveal';

type Behavior = 'ok' | 'error' | 'never';
let behavior: Behavior = 'ok';
let perUrl: Record<string, Behavior> = {};
let delayFor: Record<string, number> = {};

class FakeImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  decode = vi.fn(() => Promise.resolve());
  set src(v: string) {
    const b = perUrl[v] ?? behavior;
    if (b === 'never') return;
    if (perUrl[v]) { setTimeout(() => (b === 'ok' ? this.onload?.() : this.onerror?.()), delayFor[v] ?? 50); return; }
    setTimeout(() => (behavior === 'ok' ? this.onload?.() : this.onerror?.()), 50);
  }
}

describe('detail popup title reveal', () => {
  beforeEach(() => { perUrl = {}; delayFor = {}; vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage); });
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

  it('with the logo still pending (undefined) it holds for the wait (a logo URL may still arrive), then shows text', async () => {
    behavior = 'ok';
    const { result, rerender } = renderHook(({ url }) => useTitleReveal(url, 'a'), { initialProps: { url: undefined as string | null | undefined } });
    await flush(500);
    expect(result.current).toBe('hold');
    rerender({ url: '/late-logo.png' });
    await flush(60);
    expect(result.current).toBe('logo');
    const none = renderHook(() => useTitleReveal(undefined, 'b'));
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

  it('waits for the backdrop to decode too, so backdrop, blur and title reveal together', async () => {
    perUrl = { '/logo.png': 'ok', '/bd.jpg': 'ok' };
    delayFor = { '/logo.png': 50, '/bd.jpg': 400 };
    const { result } = renderHook(() => useTitleReveal('/logo.png', 'a', '/bd.jpg'));
    await flush(100);
    expect(result.current).toBe('hold'); // logo ready, backdrop not yet
    await flush(350);
    expect(result.current).toBe('logo');
  });

  it('a failed backdrop does not block the reveal', async () => {
    perUrl = { '/logo.png': 'ok', '/bd.jpg': 'error' };
    const { result } = renderHook(() => useTitleReveal('/logo.png', 'a', '/bd.jpg'));
    await flush(60);
    expect(result.current).toBe('logo');
  });

  it('a backdrop that never loads is shown anyway after the wait', async () => {
    perUrl = { '/logo.png': 'ok', '/bd.jpg': 'never' };
    const { result } = renderHook(() => useTitleReveal('/logo.png', 'a', '/bd.jpg'));
    await flush(LOGO_WAIT_MS - 100);
    expect(result.current).toBe('hold');
    await flush(200);
    expect(result.current).toBe('logo');
  });

  it('opening another title holds again until its own backdrop decodes', async () => {
    perUrl = { '/l1.png': 'ok', '/b1.jpg': 'ok', '/l2.png': 'ok', '/b2.jpg': 'ok' };
    delayFor = { '/b2.jpg': 300 };
    const { result, rerender } = renderHook(({ l, k, b }) => useTitleReveal(l, k, b),
      { initialProps: { l: '/l1.png', k: 'one', b: '/b1.jpg' } });
    await flush(60);
    expect(result.current).toBe('logo');
    rerender({ l: '/l2.png', k: 'two', b: '/b2.jpg' });
    await flush(100);
    expect(result.current).toBe('hold'); // never the old backdrop
    await flush(250);
    expect(result.current).toBe('logo');
  });
});
