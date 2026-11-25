/** SW 369 (Taylor 11/25, "load very smoothly with no flashing or hiccups"): a detail sheet mounts held (hidden, no
 *  scrim, taps pass through) until its one reveal is in, then opens finished; at SHEET_READY_HOLD_MS it opens anyway.
 *  A second title opened while one is held supersedes it, and Escape closes a held sheet without it ever showing. */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { AppModalHost } from '@/contexts/AppModalHost';
import { useModalHost } from '@/contexts/ModalHostContext';
import { _resetImageLoader } from '@/loading/imageLoader';
import { _resetModalPrefetch } from '@/services/modalPrefetch';
import { SHEET_READY_HOLD_MS } from '@/loading/sheetPresent';

vi.mock('@/contexts/LayoutModeContext', async (orig) => ({
  ...(await orig<typeof import('@/contexts/LayoutModeContext')>()),
  useLayoutMode: () => ({ mode: 'expanded', isCompact: false, isExpanded: true, isTablet: false, layoutMode: 'expanded' }),
}));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ implicit: false, authenticated: true, loading: false, user: { id: 1, username: 't', isAdmin: true, is_admin: true }, isAdmin: true }),
}));
class FakeImage { decoding = ''; onload: null | (() => void) = null; onerror: null | (() => void) = null; private s = '';
  set src(v: string) { this.s = v; } get src() { return this.s; } removeAttribute() { this.s = ''; } decode() { return new Promise<void>(() => {}); } }
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
function Opener() {
  const { openMovie } = useModalHost();
  return (<>
    <button type="button" onClick={() => openMovie({ tmdbId: 603, origin: 'home' })}>Open A</button>
    <button type="button" onClick={() => openMovie({ tmdbId: 680, origin: 'home' })}>Open B</button>
  </>);
}
const sheets = () => Array.from(document.querySelectorAll<HTMLElement>('[data-modal="MovieDetailModal"]'));
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
beforeEach(() => {
  _resetImageLoader(); _resetModalPrefetch(); vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('fetch', vi.fn(async () => new Promise<Response>(() => {})));   // details never land: the hold cap decides
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const host = () => render(<MemoryRouter><AppModalHost><Opener /></AppModalHost></MemoryRouter>);

describe('sheet-ready open (SW 369)', () => {
  it('a sheet mounts held: hidden, no taps, then opens at the hold cap', async () => {
    host();
    fireEvent.click(screen.getByText('Open A'));
    expect(sheets()).toHaveLength(1);
    expect(sheets()[0]!.hasAttribute('data-sheet-held')).toBe(true);
    expect(sheets()[0]!.style.visibility).toBe('hidden');
    expect(sheets()[0]!.style.pointerEvents).toBe('none');
    expect(sheets()[0]!.style.opacity).toBe('0');
    await tick(SHEET_READY_HOLD_MS - 100);
    expect(sheets()[0]!.hasAttribute('data-sheet-held')).toBe(true);
    await tick(150);
    expect(sheets()[0]!.hasAttribute('data-sheet-held')).toBe(false);
    expect(sheets()[0]!.style.visibility).toBe('');
    expect(sheets()[0]!.style.opacity).toBe('');
  });
  it('a second title opened while the first is held replaces it (no stacked, never-shown sheet)', async () => {
    host();
    fireEvent.click(screen.getByText('Open A'));
    await tick(100);
    fireEvent.click(screen.getByText('Open B'));
    await tick(10);
    expect(sheets()).toHaveLength(1);
  });
  it('Escape closes a held sheet and it never shows', async () => {
    host();
    fireEvent.click(screen.getByText('Open A'));
    await tick(100);
    fireEvent.keyDown(window, { key: 'Escape' });
    await tick(30);
    expect(sheets()[0]?.style.visibility ?? 'hidden').toBe('hidden');
    await tick(SHEET_READY_HOLD_MS * 2);
    expect(sheets()).toHaveLength(0);
  });
  it('the phone frame holds the slide too, and a held phone sheet lets taps through', () => {
    const f = readFileSync(resolve(__dirname, '../src/components/layout/SheetFrame.tsx'), 'utf8');
    expect(f).toContain("if ((!go || !present || phase === 'exiting') && !entered.current) {");
    expect(f).toContain("pointerEvents: phase === 'exiting' || (!present && !entered.current) ? 'none' : undefined");
    expect(f).toContain('settleAnimations(card);');
    expect(f).toContain("opacity: !entered.current && (!present || phase === 'exiting') ? 0 : undefined");
    const p = readFileSync(resolve(__dirname, '../src/services/modalPrefetch.ts'), 'utf8');
    expect(p).toContain(':not([data-sheet-held])');
  });
});
