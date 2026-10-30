import { act, cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SheetFrame, SheetCard, SeedShell, SHEET_EXIT_MS } from '@/components/layout/SheetFrame';
import { openWhenReady, _resetModalPrefetch } from '@/services/modalPrefetch';
import { MOTION, APP_BG } from '@/theme/motion';
import { readFileSync } from 'fs';
import { resolve } from 'path';

afterEach(() => { cleanup(); _resetModalPrefetch(); });

describe('v325 motion P1 SheetFrame', () => {
  it('portals at --z-sheet with a scrim and an opaque card; keeps data-composed for the probes', () => {
    render(<SheetFrame name="MovieDetailModal" phase="entering" composed={false} onScrimClick={() => {}}>
      <SheetCard background={APP_BG}><p>x</p></SheetCard></SheetFrame>);
    const root = document.querySelector<HTMLElement>('[data-modal="MovieDetailModal"]')!;
    expect(root).toBeTruthy();
    expect(root.style.zIndex).toBe(String(MOTION.z.sheet));
    expect(root.getAttribute('data-composed')).toBe('false');
    expect(document.querySelector('[data-sheet-scrim]')).toBeTruthy();
    const card = document.querySelector<HTMLElement>('[data-sheet-card]')!;
    expect(card.style.opacity === '' || card.style.opacity === '1').toBe(true);
  });

  it('tokens: overlay under sheet, exit 260 ms, background #111318', () => {
    expect(MOTION.z.overlay).toBeLessThan(MOTION.z.sheet);
    expect(SHEET_EXIT_MS).toBe(260);
    expect(APP_BG).toBe('#111318');
    const css = readFileSync(resolve(__dirname, '../src/index.css'), 'utf8');
    for (const v of ['--m-dur-sheet-enter', '--m-ease-decel', '--z-overlay', '--z-sheet']) expect(css).toContain(v);
  });

  it('the seed shell shows the tapped title and poster at once', async () => {
    const card = document.createElement('button');
    card.setAttribute('aria-label', 'Arrival');
    const img = document.createElement('img'); img.src = 'http://x/p.jpg'; card.appendChild(img);
    await act(async () => { await openWhenReady('movie', 5, undefined, card, () => {}); });
    const { container } = render(<SeedShell overlap={100} />);
    expect(container.textContent).toContain('Arrival');
    expect(container.querySelector('img')!.getAttribute('src')).toBe('http://x/p.jpg');
  });
});
