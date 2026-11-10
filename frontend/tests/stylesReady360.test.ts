/** The app stylesheet loads without blocking the splash's first paint, and the app renders only once it has applied. */
import { describe, expect, it } from 'vitest';
import { stylesReady } from '@/startup/styles';

describe('non-blocking app stylesheet', () => {
  it('waits for the stylesheet to load, then resolves; a failed one does not hang the app', async () => {
    document.head.innerHTML = '<link rel="stylesheet" data-luma-css media="print" href="a.css"><link rel="stylesheet" data-luma-css media="print" href="b.css">';
    const [a, b] = Array.from(document.querySelectorAll<HTMLLinkElement>('link[data-luma-css]'));
    let done = false;
    void stylesReady().then(() => { done = true; });
    await Promise.resolve();
    expect(done).toBe(false);
    a!.dispatchEvent(new Event('load'));
    await Promise.resolve();
    expect(done).toBe(false);
    b!.dispatchEvent(new Event('error'));
    await new Promise(r => setTimeout(r, 0));
    expect(done).toBe(true);
  });

  it('resolves at once when the stylesheet already applied, or there is none (dev server)', async () => {
    document.head.innerHTML = '<link rel="stylesheet" data-luma-css media="all" href="a.css">';
    await stylesReady();
    document.head.innerHTML = '';
    await stylesReady();
  });
});
