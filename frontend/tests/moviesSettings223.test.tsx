import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MoviesSettings, MovieNamingSettings, movieNamingCss, normMovieSettings } from '@/components/movies/MoviesSettings';

const IMPORT = { root: '/m', naming: { rename: true, folder: '{Movie Title} ({Release Year})', standard: '{Movie Title} ({Release Year}) {Quality Full}' } };
const stub = (data: unknown) => vi.stubGlobal('fetch', vi.fn(async (url: string) =>
  new Response(JSON.stringify(String(url).includes('/movies/import') ? IMPORT : data), { status: 200 })));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('v223 Settings > Movies guards and phone naming', () => {
  it('non-array list data (null/object) renders an empty page, no crash', async () => {
    stub({ monitoring: true, preferred_groups: null, picker_profiles: {}, trackers: null, qualities: 'x', profiles: { a: 1 } });
    render(<MoviesSettings />);
    await waitFor(() => expect(screen.getByTestId('movie-naming-settings')).toBeTruthy());
    expect(screen.queryAllByTestId('movie-profile-row')).toHaveLength(0);
    expect(screen.getByText('No trackers')).toBeTruthy();
  });
  it('missing preferred_groups reads as []', async () => {
    stub({ monitoring: true, picker_profiles: [], trackers: [], qualities: [], profiles: [] });
    render(<MoviesSettings />);
    await waitFor(() => expect(screen.getByTestId('movie-naming-settings')).toBeTruthy());
    expect(screen.getByText('Preferred groups')).toBeTruthy();
  });
  it('normMovieSettings keeps arrays and empties the rest', () => {
    const n = normMovieSettings({ preferred_groups: ['NTb'], trackers: null } as never);
    expect(n.preferred_groups).toEqual(['NTb']);
    expect(n.trackers).toEqual([]);
    expect(n.profiles).toEqual([]);
  });
  it('Folder/File format rows stack at 600px and below', async () => {
    stub({});
    render(<MovieNamingSettings />);
    await waitFor(() => expect(screen.getByTestId('mv-folder')).toBeTruthy());
    expect(screen.getByTestId('mv-folder').closest('label')!.className).toContain('mv-fmt');
    expect(screen.getByTestId('mv-file').closest('label')!.className).toContain('mv-fmt');
    expect(movieNamingCss).toContain('@media (max-width: 600px)');
    expect(movieNamingCss).toContain('flex-direction:column');
    expect(movieNamingCss).toContain('width:100%');
  });
});
