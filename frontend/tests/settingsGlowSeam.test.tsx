/** Library paths seam: the desktop settings page is flat navy with one shared
 * fixed glow layer, not a background-attachment: fixed gradient. */
import { cleanup, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it } from 'vitest';
import { GlowLayer, NAVY_BASE, NAVY_BG, glowLayer, glowPage } from '@/components/start/StartUI';
import { settingsPage } from '@/pages/LibrariesPage';

afterEach(cleanup);

describe('settings background', () => {
  it('the page is flat navy (theme token, #15102a) in its own stacking context, never a fixed gradient', () => {
    expect(NAVY_BASE).toBe('var(--color-kiosk-black)');           // Reviewer: a token, not a literal
    expect(readFileSync(`${process.cwd()}/src/index.css`, 'utf8')).toContain('--color-kiosk-black: #15102a;');
    expect(settingsPage.background).toBe(NAVY_BASE);
    expect(settingsPage.backgroundAttachment).toBeUndefined();
    expect(settingsPage.isolation).toBe('isolate');
    expect(glowPage).toEqual({ background: NAVY_BASE, isolation: 'isolate', position: 'relative' });
  });

  it('one fixed glow layer covers the viewport behind the content', () => {
    expect(glowLayer).toEqual({ position: 'fixed', inset: 0, background: NAVY_BG, pointerEvents: 'none', zIndex: -1 });
    const { getByTestId } = render(<GlowLayer />);
    const el = getByTestId('glow-layer');
    expect(el.getAttribute('aria-hidden')).toBe('true');
    expect(el.style.position).toBe('fixed');
    expect(el.style.zIndex).toBe('-1');
    expect(el.style.pointerEvents).toBe('none');
  });
});
