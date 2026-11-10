/** Phase 1 motion: the modal host keeps every open sheet mounted (a parent stays under a child), opens a title at
 *  once, treats the same title opened twice as one open, and a close that is still playing never touches the
 *  title opened right after it. A response for a title that is gone is dropped. */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppModalHost } from '@/contexts/AppModalHost';
import { useModalHost } from '@/contexts/ModalHostContext';
import { exitMs } from '@/theme/motion';
import { detailSurfaceOpen } from '@/loading/sheetOpenGate';
import { _resetImageLoader } from '@/loading/imageLoader';
import { _resetModalPrefetch } from '@/services/modalPrefetch';

const layout = vi.hoisted(() => ({ mode: 'compact' as 'compact' | 'expanded' }));
vi.mock('@/contexts/LayoutModeContext', async (orig) => ({
  ...(await orig<typeof import('@/contexts/LayoutModeContext')>()),
  useLayoutMode: () => ({ mode: layout.mode, isCompact: layout.mode === 'compact', isExpanded: layout.mode === 'expanded', isTablet: false, layoutMode: layout.mode }),
}));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ implicit: false, authenticated: true, loading: false, user: { id: 1, username: 't', isAdmin: true, is_admin: true }, isAdmin: true }),
}));

class FakeImage {
  decoding = '';
  onload: null | (() => void) = null;
  onerror: null | (() => void) = null;
  private s = '';
  set src(v: string) { this.s = v; }
  get src() { return this.s; }
  removeAttribute() { this.s = ''; }
  decode() { return new Promise<void>(() => {}); }
}

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const movie = (id: number, title: string) => json({ id, title, release_date: '1999-01-01', backdrop_path: `/${id}.jpg`, videos: { results: [] } });
let releaseMatrix: () => void = () => {};

function Opener() {
  const { openMovie } = useModalHost();
  return (
    <>
      <button type="button" onClick={() => openMovie({ tmdbId: 603, origin: 'home' })}>Open A</button>
      <button type="button" onClick={() => openMovie({ tmdbId: 680, origin: 'home' })}>Open B</button>
    </>
  );
}

const sheets = () => Array.from(document.querySelectorAll<HTMLElement>('[data-modal="MovieDetailModal"]'));
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

beforeEach(() => {
  layout.mode = 'compact';
  _resetImageLoader();
  _resetModalPrefetch();
  vi.useFakeTimers();
  vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u);
    if (url.includes('/api/movie/603')) { await new Promise<void>((res) => { releaseMatrix = res; }); return movie(603, 'The Matrix'); }
    if (url.includes('/api/movie/680')) return movie(680, 'Pulp Fiction');
    if (url.includes('/api/clearlogo/')) return json({ clearlogo_url: null });
    if (url.includes('/api/plex/lookup')) return json({ owned: false });
    return json({});
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const host = () => render(
  <MemoryRouter><AppModalHost><Opener /></AppModalHost></MemoryRouter>,
);

describe.each(['compact', 'expanded'] as const)('modal host (%s)', (mode) => {
  beforeEach(() => { layout.mode = mode; });

  it('opens the sheet in the tap\'s own commit, before any data', () => {
    host();
    fireEvent.click(screen.getByText('Open A'));
    expect(sheets()).toHaveLength(1);
    expect(detailSurfaceOpen()).toBe(true);
  });

  it('the same title opened twice is one open', () => {
    host();
    fireEvent.click(screen.getByText('Open A'));
    fireEvent.click(screen.getByText('Open A'));
    expect(sheets()).toHaveLength(1);
  });

  it('a parent sheet stays mounted (same node) under a child, and is what is left when the child closes', async () => {
    host();
    fireEvent.click(screen.getByText('Open A'));
    const [parent] = sheets();
    fireEvent.click(screen.getByText('Open B'));
    expect(sheets()).toHaveLength(2);
    expect(sheets()[0]).toBe(parent);
    fireEvent.keyDown(window, { key: 'Escape' });
    await tick(exitMs(mode === 'compact' ? 'sheet' : 'modal') + 50);
    expect(sheets()).toHaveLength(1);
    expect(sheets()[0]).toBe(parent);
  });

  it('only the top sheet answers Escape; the parent under it stays open', async () => {
    host();
    fireEvent.click(screen.getByText('Open A'));
    fireEvent.click(screen.getByText('Open B'));
    fireEvent.keyDown(window, { key: 'Escape' });
    await tick(900);
    expect(sheets()).toHaveLength(1);
    fireEvent.keyDown(window, { key: 'Escape' });
    await tick(900);
    expect(sheets()).toHaveLength(0);
  });

  it('a close that is still playing never closes the title opened right after it', async () => {
    host();
    fireEvent.click(screen.getByText('Open B'));
    fireEvent.keyDown(window, { key: 'Escape' });
    await tick(60);
    expect(sheets()[0]?.getAttribute('data-phase')).toBe('exiting');
    fireEvent.click(screen.getByText('Open A'));
    await tick(exitMs(mode === 'compact' ? 'sheet' : 'modal') * 3);
    expect(sheets()).toHaveLength(1);
    expect(sheets()[0]?.getAttribute('data-phase')).not.toBe('exiting');
  });

  it('a response for a title that was closed and replaced is dropped', async () => {
    host();
    fireEvent.click(screen.getByText('Open A'));
    await tick(10);
    fireEvent.keyDown(window, { key: 'Escape' });
    await tick(exitMs(mode === 'compact' ? 'sheet' : 'modal') + 50);
    expect(sheets()).toHaveLength(0);
    fireEvent.click(screen.getByText('Open B'));
    await tick(50);
    await act(async () => { releaseMatrix(); await vi.advanceTimersByTimeAsync(50); });
    expect(screen.queryByText('The Matrix')).toBeNull();
    expect(screen.getAllByText('Pulp Fiction').length).toBeGreaterThan(0);
  });
});

describe('rapid open, close and switch', () => {
  it('twenty alternating open and close taps never leave a stuck, doubled or stale sheet', async () => {
    host();
    for (let i = 0; i < 20; i++) {
      fireEvent.click(screen.getByText(i % 2 ? 'Open A' : 'Open B'));
      await tick(20 + (i % 5) * 30);
      fireEvent.keyDown(window, { key: 'Escape' });
      await tick(15);
    }
    await tick(2000);
    expect(sheets().filter((s) => s.getAttribute('data-phase') !== 'exiting').length).toBeLessThanOrEqual(1);
    fireEvent.click(screen.getByText('Open B'));
    await tick(20);
    expect(sheets().filter((s) => s.getAttribute('data-phase') !== 'exiting')).toHaveLength(1);
  });
});
