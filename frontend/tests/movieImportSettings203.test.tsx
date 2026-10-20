import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MovieImportSettings } from '@/components/movies/MoviesSettings';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('v203 Settings > Movies: Library path and naming', () => {
  it('pre-fills and saves', async () => {
    const view = { root: '/media/Movies', naming: { rename: true, folder: '{Movie Title} ({Release Year})', standard: '{Movie Title} ({Release Year})' } };
    const puts: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init?: RequestInit) => {
      if (init?.method === 'PUT') { puts.push(JSON.parse(String(init.body))); return new Response(JSON.stringify({ ...view, root: '/m2' }), { status: 200 }); }
      return new Response(JSON.stringify(view), { status: 200 });
    }));
    render(<MovieImportSettings />);
    await waitFor(() => expect((screen.getByTestId('mv-root') as HTMLInputElement).value).toBe('/media/Movies'));
    expect((screen.getByTestId('mv-file') as HTMLInputElement).value).toBe('{Movie Title} ({Release Year})');
    fireEvent.change(screen.getByTestId('mv-root'), { target: { value: '/m2' } });
    fireEvent.click(screen.getByTestId('mv-import-save'));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ root: '/m2', naming: { rename: true, folder: view.naming.folder, standard: view.naming.standard } });
  });
});
