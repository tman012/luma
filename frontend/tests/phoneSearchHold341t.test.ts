import { describe, it, expect, beforeEach } from 'vitest';
import { beginSheetPriority, endSheetPriority, keepHomePostersOnOpen, _resetSheetPriority } from '@/loading/sheetPriority';

function img(parentTestId: string): HTMLImageElement {
  const d = document.createElement('div'); d.setAttribute('data-testid', parentTestId);
  const i = document.createElement('img'); i.setAttribute('src', '/api/img/tmdb?path=%2Fx.jpg&size=w342');
  Object.defineProperty(i, 'complete', { value: false });
  i.getBoundingClientRect = () => ({ width: 60, height: 90, top: 10, left: 10, bottom: 100, right: 70 } as DOMRect);
  d.appendChild(i); document.body.appendChild(d); return i;
}
describe('v341t phone search hold (Tester PREP 46d2e5ee)', () => {
  beforeEach(() => { _resetSheetPriority(); document.body.innerHTML = ''; });
  it('strips on-screen SearchShelf posters even when the open keeps on-screen posters', () => {
    const shelf = img('search-shelf'); const home = img('card-x');
    keepHomePostersOnOpen(true); beginSheetPriority();
    expect(shelf.hasAttribute('src')).toBe(false);
    expect(home.getAttribute('src')).not.toBeNull();
    endSheetPriority();
  });
});
