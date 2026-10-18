/** v179: a tab left open across a deploy reloads into the new build (Taylor's v178 report: a v177 tab drew
 * v178's still-less calendar data in v177's reserved 16:9 boxes). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BUILD_CHECK_MS, BUILD_FOCUS_GAP_MS, checkBuild, entryOf, runningEntry, watchBuild } from '@/utils/buildWatch';

const shell = (entry: string, appShell = true, ok = true) => vi.fn(async () => ({
  ok, status: ok ? 200 : 503,
  headers: new Headers(appShell ? { 'X-Luma-App-Shell': '1' } : {}),
  text: async () => `<html><head><script type="module" crossorigin src="${entry}"></script></head></html>`,
}) as unknown as Response);
const mem = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v); } }; };

beforeEach(() => {
  document.head.innerHTML = '<script type="module" crossorigin src="/assets/index-OLD177aa.js"></script>';
  document.body.innerHTML = '<input id="f" />';
});
afterEach(() => { document.head.innerHTML = ''; document.body.innerHTML = ''; vi.useRealTimers(); });

describe('build watch', () => {
  it('reads the entry bundle from a page and from this tab', () => {
    expect(entryOf('<script src="/luma/assets/index-CB6liDyL.js">')).toBe('/assets/index-CB6liDyL.js');
    expect(entryOf('<p>Sign in</p>')).toBeNull();
    expect(runningEntry()).toBe('/assets/index-OLD177aa.js');
  });
  it('a new build on the server reloads the tab once; the same build does nothing', async () => {
    const reload = vi.fn(); const storage = mem();
    expect(await checkBuild({ base: '', fetchImpl: shell('/assets/index-OLD177aa.js'), reload, storage })).toBe('same');
    expect(reload).not.toHaveBeenCalled();
    const f = shell('/assets/index-NEW179bb.js');
    expect(await checkBuild({ base: '/luma', fetchImpl: f, reload, storage })).toBe('reloaded');
    expect(f).toHaveBeenCalledWith('/luma/', expect.objectContaining({ cache: 'no-store', credentials: 'same-origin' }));
    expect(reload).toHaveBeenCalledTimes(1);
    // a reload that still came back on the old bundle never loops
    expect(await checkBuild({ base: '', fetchImpl: f, reload, storage })).toBe('skipped');
    expect(reload).toHaveBeenCalledTimes(1);
  });
  it('never reloads while a field has focus, offline, on an error or on a page that is not the app', async () => {
    const reload = vi.fn();
    (document.getElementById('f') as HTMLInputElement).focus();
    expect(await checkBuild({ base: '', fetchImpl: shell('/assets/index-NEW179bb.js'), reload, storage: mem() })).toBe('deferred');
    (document.getElementById('f') as HTMLInputElement).blur();
    expect(await checkBuild({ base: '', fetchImpl: vi.fn(async () => { throw new TypeError('offline'); }), reload, storage: mem() })).toBe('skipped');
    expect(await checkBuild({ base: '', fetchImpl: shell('/assets/index-NEW179bb.js', true, false), reload, storage: mem() })).toBe('skipped');
    expect(await checkBuild({ base: '', fetchImpl: shell('/assets/index-NEW179bb.js', false), reload, storage: mem() })).toBe('skipped');
    expect(reload).not.toHaveBeenCalled();
  });
  it('the dev server (no hashed entry) is left alone', async () => {
    document.head.innerHTML = '<script type="module" src="/src/main.tsx"></script>';
    const f = shell('/assets/index-NEW179bb.js');
    expect(await checkBuild({ base: '', fetchImpl: f, reload: vi.fn(), storage: mem() })).toBe('skipped');
    expect(f).not.toHaveBeenCalled();
  });
  it('checks on focus (throttled) and every 5 minutes while visible', async () => {
    vi.useFakeTimers();
    let t = 0; const reload = vi.fn();
    const f = shell('/assets/index-OLD177aa.js');
    const stop = watchBuild({ base: '', fetchImpl: f, reload, storage: mem(), now: () => t });
    window.dispatchEvent(new Event('focus'));
    expect(f).toHaveBeenCalledTimes(0);                                  // right after start: throttled
    t += BUILD_FOCUS_GAP_MS + 1;
    window.dispatchEvent(new Event('focus'));
    await vi.advanceTimersByTimeAsync(0);
    expect(f).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(BUILD_CHECK_MS);
    expect(f).toHaveBeenCalledTimes(2);
    stop();
    await vi.advanceTimersByTimeAsync(BUILD_CHECK_MS * 2);
    expect(f).toHaveBeenCalledTimes(2);
    expect(reload).not.toHaveBeenCalled();
  });
});
