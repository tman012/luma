import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { MoviesSettings, MovieNamingSettings } from '@/components/movies/MoviesSettings';

const DATA = {
  monitoring: true, preferred_groups: [], picker_profiles: [],
  trackers: [{ id: 1, name: 'BeyondHD', enabled: true }],
  qualities: [{ id: '1080p-web', name: '1080p WEB' }],
  profiles: [{ id: 1, name: 'Any', items: [{ id: '1080p-web', allowed: true }], cutoff: '1080p-web', min_mb_per_min: null, max_mb_per_min: null, default: true }],
};
const IMPORT = { root: '/media/Movies', naming: { rename: false, folder: '{Movie Title} ({Release Year})', standard: '{Movie Title} ({Release Year}) {Quality Full}' } };

function stub(puts: { url: string; body: unknown }[]) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const isImport = String(url).includes('/movies/import');
    if (init?.method === 'PUT') {
      const b = JSON.parse(String(init.body)); puts.push({ url: String(url), body: b });
      return new Response(JSON.stringify(isImport ? { ...IMPORT, naming: b.naming } : { ...DATA, ...b }), { status: 200 });
    }
    return new Response(JSON.stringify(isImport ? IMPORT : DATA), { status: 200 });
  }));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('v220 Settings > Movies cleanup', () => {
  it('shows only Quality profiles, Watchlist picker, Trackers, Preferred groups, Naming, in that order', async () => {
    stub([]);
    render(<MoviesSettings />);
    await waitFor(() => expect(screen.getByTestId('movie-naming-settings')).toBeTruthy());
    const heads = Array.from(screen.getByTestId('movies-settings').querySelectorAll('h3')).map(h => h.textContent);
    expect(heads).toEqual(['Quality profiles', 'Watchlist picker', 'Trackers', 'Preferred groups', 'Naming']);
  });
  it('has no Import card, Library path, Rename movies or Monitoring', async () => {
    stub([]);
    render(<MoviesSettings />);
    await waitFor(() => expect(screen.getByTestId('movie-naming-settings')).toBeTruthy());
    expect(screen.queryByTestId('movie-import-settings')).toBeNull();
    expect(screen.queryByTestId('mv-root')).toBeNull();
    const text = document.body.textContent || '';
    for (const t of ['Import', 'Library path', 'Rename movies', 'Monitoring']) expect(text).not.toContain(t);
  });
  it('Naming saves folder and file format to the same keys, keeps the stored rename and never sends root', async () => {
    const puts: { url: string; body: unknown }[] = [];
    stub(puts);
    render(<MovieNamingSettings />);
    await waitFor(() => expect((screen.getByTestId('mv-folder') as HTMLInputElement).value).toBe(IMPORT.naming.folder));
    fireEvent.change(screen.getByTestId('mv-folder'), { target: { value: '{Movie Title}' } });
    fireEvent.click(screen.getByTestId('mv-naming-save'));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!.url).toContain('/api/settings/movies/import');
    expect(puts[0]!.body).toEqual({ naming: { rename: false, folder: '{Movie Title}', standard: IMPORT.naming.standard } });
  });
  it('SW is media-kiosk-v220', () => {
    expect(readFileSync(resolve(__dirname, '../../static/sw.js'), 'utf8')).toContain("const CACHE_VERSION = 'media-kiosk-v220';");
  });
});
