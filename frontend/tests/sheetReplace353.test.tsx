/** A second title tapped while the first sheet is still closed replaces it.
 *  The host must not stack the hidden modal and restore it on close. */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppModalHost } from '@/contexts/AppModalHost';
import { LayoutModeProvider } from '@/contexts/LayoutModeContext';
import { useModalHost } from '@/contexts/ModalHostContext';
import { SHEET_EXIT_MS } from '@/components/layout/SheetFrame';
import { detailSurfaceOpen, SHEET_OPEN_BOUND_MS } from '@/loading/sheetOpenGate';
import { _resetImageLoader } from '@/loading/imageLoader';
import { _resetModalPrefetch } from '@/services/modalPrefetch';

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

function Opener() {
  const { openMovie } = useModalHost();
  return (
    <>
      <button type="button" onClick={() => openMovie({ tmdbId: 603, origin: 'home' })}>Open A</button>
      <button type="button" onClick={() => openMovie({ tmdbId: 680, origin: 'home' })}>Open B</button>
    </>
  );
}

beforeEach(() => {
  _resetImageLoader();
  _resetModalPrefetch();
  vi.useFakeTimers();
  vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u);
    if (url.includes('/api/movie/603')) return movie(603, 'The Matrix');
    if (url.includes('/api/movie/680')) return movie(680, 'Pulp Fiction');
    if (url.includes('/api/clearlogo/')) return json({ clearlogo_url: null });
    if (url.includes('/api/plex/lookup')) return json({ owned: false });
    return json({});
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('a hidden detail open is replaced, not stacked', () => {
  it('detailSurfaceOpen is false until a sheet is portaled', () => {
    expect(detailSurfaceOpen()).toBe(false);
    const el = document.createElement('div');
    el.setAttribute('data-modal', 'MovieDetailModal');
    document.body.appendChild(el);
    expect(detailSurfaceOpen()).toBe(true);
    el.remove();
    expect(detailSurfaceOpen()).toBe(false);
  });

  it('closing the second title does not restore the first', async () => {
    render(
      <MemoryRouter>
        <LayoutModeProvider>
          <AppModalHost>
            <Opener />
          </AppModalHost>
        </LayoutModeProvider>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText('Open A'));
    await act(async () => { await vi.advanceTimersByTimeAsync(30); });
    expect(document.querySelector('[data-modal="MovieDetailModal"]')).toBeNull();
    const matrixFetches = () => (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.filter(c => String(c[0]).includes('/api/movie/603')).length;
    const before = matrixFetches();
    expect(before).toBeGreaterThan(0);
    fireEvent.click(screen.getByText('Open B'));
    await act(async () => { await vi.advanceTimersByTimeAsync(SHEET_OPEN_BOUND_MS + SHEET_EXIT_MS + 80); });
    expect(document.querySelector('[data-modal="MovieDetailModal"]')).toBeNull();
    expect(document.documentElement.dataset.detailMounted).toBeUndefined();
    expect(screen.queryByText('The Matrix')).toBeNull();
    expect(screen.queryByText('Pulp Fiction')).toBeNull();
    expect(matrixFetches()).toBe(before);
  });
});
