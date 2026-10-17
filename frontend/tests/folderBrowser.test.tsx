import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PathForm } from '@/components/library/LibraryPathsSettings';
import { FolderBrowser } from '@/components/library/FolderBrowser';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const TREE: Record<string, { parent: string | null; dirs: string[] }> = {
  '': { parent: null, dirs: ['/media', '/storage/downloads'] },
  '/media': { parent: null, dirs: ['Video', 'TV'] },
  '/media/Video': { parent: '/media', dirs: ['Movies'] },
  '/media/Video/Movies': { parent: '/media/Video', dirs: [] },
};
const urls: string[] = [];

beforeEach(() => {
  urls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u);
    urls.push(url);
    const p = new URL(url, 'http://x').searchParams.get('path') || '';
    if (p === '/etc') return res({ error: 'Not allowed' }, 400);
    const n = TREE[p];
    if (!n) return res({ error: 'Folder not found' }, 400);
    const dirs = p ? n.dirs.map(d => ({ name: d, path: `${p}/${d}` })) : n.dirs.map(d => ({ name: d, path: d }));
    return res({ path: p || null, parent: n.parent, dirs });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('folder picker', () => {
  it('starts at the roots, opens folders, goes back, and picks', async () => {
    const onPick = vi.fn();
    render(<FolderBrowser start={null} onPick={onPick} onClose={() => {}} />);
    await screen.findByText('/storage/downloads');
    expect(screen.getByTestId('lp-browser-path').textContent).toBe('Folders');
    expect((screen.getByTestId('lp-browser-use') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText('/media'));
    await screen.findByText('Video');
    fireEvent.click(screen.getByText('Video'));
    await screen.findByText('Movies');
    expect(screen.getByTestId('lp-browser-path').textContent).toBe('/media/Video');
    fireEvent.click(screen.getByTestId('lp-browser-up'));
    await screen.findByText('TV');
    fireEvent.click(screen.getByText('Video'));
    await screen.findByText('Movies');
    fireEvent.click(screen.getByTestId('lp-browser-use'));
    expect(onPick).toHaveBeenCalledWith('/media/Video');
  });

  it('a typed folder outside the roots starts at the roots', async () => {
    render(<FolderBrowser start="/etc" onPick={() => {}} onClose={() => {}} />);
    await screen.findByText('/media');
    expect(urls[0]).toContain('path=%2Fetc');
    expect(urls[1]).toMatch(/browse$/);
  });

  it('PathForm: Browse fills the Folder field', async () => {
    const onSave = vi.fn(async () => ({ ok: true }));
    render(<PathForm first onSave={onSave} onCancel={() => {}} />);
    fireEvent.change(screen.getByTestId('lp-name'), { target: { value: 'Movies' } });
    fireEvent.click(screen.getByTestId('lp-browse'));
    fireEvent.click(await screen.findByText('/media'));
    fireEvent.click(await screen.findByText('Video'));
    fireEvent.click(await screen.findByText('Movies'));
    await waitFor(() => expect(screen.getByTestId('lp-browser-path').textContent).toBe('/media/Video/Movies'));
    fireEvent.click(screen.getByTestId('lp-browser-use'));
    expect((screen.getByTestId('lp-dir') as HTMLInputElement).value).toBe('/media/Video/Movies');
    expect(screen.queryByTestId('lp-browser')).toBeNull();
    await act(async () => { fireEvent.click(screen.getByTestId('lp-save')); });
    expect(onSave).toHaveBeenCalledWith('Movies', '/media/Video/Movies');
  });
});
