// @vitest-environment node
/** The build loads the app stylesheet without blocking the splash's first paint (src/startup/styles.ts waits for it). */
import { describe, expect, it } from 'vitest';
import { nonBlockingCss } from '../vite.config';

describe('non-blocking app stylesheet (build)', () => {
  it('the build rewrites the render-blocking link and keeps a noscript copy', () => {
    const plugin = nonBlockingCss();
    const handler = (plugin.transformIndexHtml as { handler: (h: string) => string }).handler;
    const out = handler('<head><link rel="stylesheet" crossorigin href="./assets/index-abc.css"></head>');
    expect(out).toContain('href="./assets/index-abc.css" media="print" data-luma-css onload="this.media=\'all\';this.dataset.loaded=\'1\'"');
    expect(out).toContain('onerror="this.media=\'all\'');
    expect(out).toContain('<noscript><link rel="stylesheet" crossorigin href="./assets/index-abc.css"></noscript>');
    expect(handler('<link rel="modulepreload" href="./assets/x.js">')).toBe('<link rel="modulepreload" href="./assets/x.js">');
  });
});
