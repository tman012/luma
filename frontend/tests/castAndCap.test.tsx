import { act, render, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CastPhoto } from '@/components/media/CastPhoto';
import { castThumb, CAST_THUMB_W } from '@/utils/image';
import { getCastImageUrl } from '@/utils/modalParsers';
import { openWhenReady, remainingCap, OPEN_CAP_MS, _resetModalPrefetch } from '@/services/modalPrefetch';
import { useTitleReveal } from '@/hooks/useTitleReveal';
import { warmResults, WARM_FULL_ROWS } from '@/services/searchApi';
import * as prefetch from '@/services/modalPrefetch';

// Images that "load" only when told to, so tests control timing.
const imgs: { src: string; onload: (() => void) | null }[] = [];
class FakeImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  decoding = '';
  complete = false;
  naturalWidth = 0;
  private _src = '';
  set src(v: string) { this._src = v; imgs.push(this as unknown as { src: string; onload: (() => void) | null }); }
  get src() { return this._src; }
  decode() { return Promise.resolve(); }
}

describe('cast photos never gate the reveal', () => {
  beforeEach(() => { imgs.length = 0; vi.stubGlobal('Image', FakeImage); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('shows the placeholder and starts no download before the reveal', () => {
    const { container } = render(<CastPhoto src="/p.jpg" alt="A" ready={false} fallback="/f.svg" style={{}} />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('[data-cast-photo="placeholder"]')).toBeTruthy();
    expect(imgs.length).toBe(0);
  });

  it('after the reveal loads off-screen and swaps in only once decoded (never a pending image in view)', async () => {
    const { container } = render(<CastPhoto src="/p.jpg" alt="A" ready fallback="/f.svg" style={{}} />);
    expect(imgs.map((i) => i.src)).toEqual(['/p.jpg']);
    expect(container.querySelector('img')).toBeNull();
    await act(async () => { imgs[0]!.onload?.(); await Promise.resolve(); });
    expect(container.querySelector('img')?.getAttribute('src')).toBe('/p.jpg');
  });
});

describe('cast photo URLs', () => {
  it('Plex people photos become a 120px proxy transcode, never the plex.tv original', () => {
    const u = 'https://metadata-static.plex.tv/b/people/b604d520903d1d1f445458008ab9d1f3.jpg';
    const out = castThumb(u)!;
    expect(out).toContain('/api/img/plex?url=');
    expect(out).toContain(`w=${CAST_THUMB_W}`);
    expect(out.startsWith('https://metadata-static')).toBe(false);
    expect(getCastImageUrl({ name: 'x', thumb: u } as never)).toBe(out);
  });
  it('other hosts are left alone (no open proxy)', () => {
    expect(castThumb('https://example.com/a.jpg')).toBe('https://example.com/a.jpg');
  });
});

describe('the 600 ms cap counts from the tap', () => {
  beforeEach(() => { _resetModalPrefetch(); vi.useFakeTimers(); vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {}))); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('time spent before the sheet mounts (detail fetch) comes off the reveal cap', async () => {
    const open = vi.fn();
    void openWhenReady('movie', 7, undefined, null, open);
    await act(async () => { await vi.advanceTimersByTimeAsync(OPEN_CAP_MS); });
    expect(open).toHaveBeenCalled();
    expect(remainingCap(600)).toBe(0);
    // A title that mounts now reveals (text fallback) at once, not 600 ms later.
    const { result } = renderHook(() => useTitleReveal(undefined, 'T'));
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(result.current).toBe('text');
  });

  it('without a recent tap the hooks keep their own default', () => {
    expect(remainingCap(500)).toBe(500);
  });
});

describe('phone search warms rows on screen', () => {
  afterEach(() => { vi.restoreAllMocks(); });
  it(`warms the top ${WARM_FULL_ROWS} fully and the next ones' details only`, () => {
    const spy = vi.spyOn(prefetch, 'scheduleWarm').mockImplementation(() => () => {});
    const items = Array.from({ length: 12 }, (_, i) => ({ id: i + 1, title: `T${i}`, media_type: 'movie' as const }));
    vi.useFakeTimers();
    warmResults(items as never);
    expect(spy).not.toHaveBeenCalled();   // after the results settle (SEARCH_WARM_DEBOUNCE_MS)
    vi.advanceTimersByTime(250);
    vi.useRealTimers();
    const keys = spy.mock.calls.map((c) => c[0]);
    expect(keys.length).toBe(10);
    expect(keys.filter((k) => k.endsWith(':full')).length).toBe(WARM_FULL_ROWS);
    expect(keys.slice(WARM_FULL_ROWS).every((k) => k.endsWith(':meta'))).toBe(true);
  });
});
