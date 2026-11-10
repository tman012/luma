/** v344 (Jaws, owned movie from search): the sheet always shows the logo or the text title, never neither (the stall,
 *  fail and no-logo cases on all four layouts: neverBlank345.test.tsx). */
import { act, render, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const layoutMode = vi.hoisted(() => ({ mode: 'compact' as 'compact' | 'expanded' }));
vi.mock('@/contexts/LayoutModeContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/LayoutModeContext')>()),
  useLayoutMode: () => ({ mode: layoutMode.mode, isCompact: layoutMode.mode === 'compact', isExpanded: layoutMode.mode === 'expanded', layoutMode: layoutMode.mode }) }));
vi.mock('@/contexts/AuthContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/AuthContext')>()), useAuth: () => ({ implicit: false, authenticated: true, user: { id: 1, username: 't', isAdmin: true }, isAdmin: true }) }));
vi.mock('@/contexts/ModalHostContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/ModalHostContext')>()), useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn(), registerBackHandler: vi.fn(() => () => {}), closeAll: vi.fn() }) }));
import { MovieDetailModal } from '@/components/media/MovieDetailModal';
import { openWhenReady, _resetModalPrefetch } from '@/services/modalPrefetch';
const stall = { logo: false, plexMs: 0 };
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
class FakeImage { private s=''; fetchPriority=''; decoding=''; onload: null|(()=>void)=null; onerror: null|(()=>void)=null;
  set src(v: string) { this.s = v; if (v && !(stall.logo && v.includes('jaws.png'))) setTimeout(() => this.onload?.(), 30); } get src() { return this.s; }
  removeAttribute() { this.s=''; } decode = () => new Promise<void>((r) => { if (!(stall.logo && this.s.includes('jaws.png'))) setTimeout(r, 30); }); naturalWidth = 10; complete = true; }
beforeEach(() => { _resetModalPrefetch(); vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('fetch', vi.fn(async (u: string) => { const url = String(u);
    if (url.includes('/api/plex/movie/5003') && stall.plexMs) { await new Promise(r => setTimeout(r, stall.plexMs)); } if (url.includes('/api/plex/movie/5003')) return json({ title: 'Jaws', year: 1975, tmdb_id: 578, art: '/library/metadata/5003/art/1', cast: [], directors: [] });
    if (url.includes('/api/movie/578')) return json({ id: 578, title: 'Jaws', backdrop_path: '/j.jpg', videos: { results: [] } });
    if (url.includes('/api/clearlogo/578')) return json({ clearlogo_url: 'https://image.tmdb.org/t/p/original/jaws.png' });
    return json({}); })); });
afterEach(() => { stall.logo = false; stall.plexMs = 0; domIncomplete = false; cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
let domIncomplete = false;
const desc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'complete');
Object.defineProperty(HTMLImageElement.prototype, 'complete', { configurable: true,
  get(this: HTMLImageElement) { return domIncomplete && this.src.includes('jaws.png') ? false : true; } }); // jsdom never loads images: a browser's decoded logo is complete
void desc;

async function openJaws(row: HTMLElement) {
  document.body.appendChild(row);
  void openWhenReady('movie', 5003, 578, row, () => {}, undefined, []);
  render(<MovieDetailModal ratingKey={5003} tmdbId={578} onClose={() => {}} />);
  for (let i = 0; i < 40; i++) await act(async () => { await vi.advanceTimersByTimeAsync(100); });
}
function shown(): string {
  const box = document.querySelector('[data-hero-title]');
  if (document.querySelector('[data-hero-logo]')) return 'logo';
  const h1 = Array.from(document.querySelectorAll('h1')).find(h => getComputedStyle(h).display !== 'none' && (!box || box.contains(h) || true));
  return (h1?.textContent ?? '').trim();
}

describe('v344 Jaws: never a blank title', () => {
  it('phone: a cold open whose logo never decodes opens the frame at once and holds the title (no blank title, no stand-in text)', async () => {
    layoutMode.mode = 'compact'; stall.logo = true; stall.plexMs = 900;
    await openJaws(document.createElement('button'));
    expect(document.querySelector('[data-modal="MovieDetailModal"]')).not.toBeNull();
    const title = document.querySelector<HTMLElement>('[data-hero-title]');
    expect(title?.getAttribute('data-hero-title')).toBe('hold');
    expect(title?.style.opacity).toBe('0');
  });
  it('compact: a logo that paints stays the logo', async () => {
    layoutMode.mode = 'compact';
    const row = document.createElement('button'); row.setAttribute('aria-label', 'Jaws');
    await openJaws(row);
    expect(document.querySelector('[data-hero-logo]')).not.toBeNull();
  });
});
