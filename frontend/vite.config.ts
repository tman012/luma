import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { resolve } from 'path';
import type { Plugin } from 'vite';

/** The built stylesheet is render-blocking, which would hold the splash's first paint until the whole app CSS arrives.
 *  Load it without blocking; src/startup/styles.ts makes the app wait for it before rendering, so the app still never
 *  paints unstyled and the splash (static HTML/CSS in index.html) paints at once. */
export function nonBlockingCss(): Plugin {
  return {
    name: 'luma-non-blocking-css',
    apply: 'build',
    transformIndexHtml: {
      order: 'post',
      handler(html) {
        return html.replace(/<link rel="stylesheet"([^>]*?) href="([^"]+\.css)">/g, (_m, attrs: string, href: string) =>
          `<link rel="stylesheet"${attrs} href="${href}" media="print" data-luma-css onload="this.media='all';this.dataset.loaded='1'" onerror="this.media='all';this.dataset.loaded='1'">`
          + `<noscript><link rel="stylesheet"${attrs} href="${href}"></noscript>`);
      },
    },
  };
}

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    nonBlockingCss(),
  ],
  // Relative base: the bundle works under any URL base (e.g. /luma). The
  // server rewrites ./assets/ in index.html to <URL_BASE>/assets/.
  base: './',
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
      '/static': {
        target: 'http://localhost:5001',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router'],
        },
      },
    },
  },
});
