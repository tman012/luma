/** PM ruling: deleting a download's files needs the owner signed in with a
 * session, even with LAN sign-in off. Activity offers a sign-in instead. */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const auth = { implicit: false };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn() }) }));

import { ActivityFeed, PATH_NOT_MAPPED, NO_WRITABLE_DEST, CARD_BG, CARD_BORDER, NO_ART_BG, SIGN_IN_BUTTON, SIGN_IN_TO_DELETE, removeDownload } from '@/components/activity/ActivityFeed';

const H = 'a'.repeat(40);
const QUEUE = { downloads: [{ name: 'Some.Show.S01.1080p.WEB-GRP', hash: H, progress: 0.5, status: 'downloading',
  size_total: 10, size_remaining: 5, speed: 1, media_type: 'tv' }] };
let removeStatus = 200;
const calls: string[] = [];
const res = (body: unknown, status = 200) => ({ ok: status < 300, status, statusText: '', json: async () => body }) as Response;

function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname}|{JSON.stringify(loc.state)}</div>;
}

beforeEach(() => {
  auth.implicit = false; removeStatus = 200; calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes('/api/downloads/remove/')) {
      calls.push(`${init?.method} ${u.slice(u.indexOf('/api/'))}`);
      return removeStatus === 401 ? res({ error: 'Sign in first', sign_in: true }, 401) : res({ success: true, deleteFiles: true });
    }
    if (u.includes('/api/downloads/queue')) return res(QUEUE);
    if (u.includes('/api/downloads/history')) return res({ entries: [] });
    return res({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openRow() {
  render(
    <MemoryRouter initialEntries={['/activity']}>
      <Routes>
        <Route path="/activity" element={<ActivityFeed />} />
        <Route path="/login" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  const name = await screen.findByText('Some.Show.S01.1080p.WEB-GRP');
  await act(async () => { fireEvent.click(name); });
}

describe('Activity: delete torrent + files', () => {
  it('the implicit LAN owner gets a sign-in prompt instead of the delete', async () => {
    auth.implicit = true;
    await openRow();
    const btn = screen.getByTestId('activity-sign-in');
    expect(btn.textContent).toBe(SIGN_IN_TO_DELETE);
    expect(SIGN_IN_TO_DELETE).toBe('Sign in to delete files');
    expect(screen.queryByText('Delete torrent + files')).toBeNull();
    await act(async () => { fireEvent.click(btn); });
    expect(screen.getByTestId('where').textContent).toBe('/login|{"from":"/activity","signIn":true}');
    expect(calls).toEqual([]);
  });

  it('a signed-in owner deletes as before', async () => {
    await openRow();
    expect(screen.queryByTestId('activity-sign-in')).toBeNull();
    await act(async () => { fireEvent.click(screen.getByText('Delete torrent + files')); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete torrent + files' })); });
    await waitFor(() => expect(calls).toEqual([`DELETE /api/downloads/remove/${H}?deleteFiles=true`]));
  });

  it('a 401 brings the row back with the sign-in prompt, no error text', async () => {
    removeStatus = 401;
    await openRow();
    await act(async () => { fireEvent.click(screen.getByText('Delete torrent + files')); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete torrent + files' })); });
    const name = await screen.findByText('Some.Show.S01.1080p.WEB-GRP');
    if (!screen.queryByTestId('activity-sign-in')) await act(async () => { fireEvent.click(name); });
    expect((await screen.findByTestId('activity-sign-in')).textContent).toBe('Sign in to delete files');
    expect(screen.queryByText(/Remove failed|Sign in first/)).toBeNull();
  });

  it('removeDownload reads a 401 as sign in only for a files delete', async () => {
    removeStatus = 401;
    expect(await removeDownload(H, true)).toEqual({ ok: false, signIn: true });
    expect((await removeDownload(H, false)).signIn).toBeUndefined();
  });
});

describe('Activity card: navy glass tokens (Designer)', () => {
  it('card, border, no-art tile and sign-in button use the tokens', async () => {
    auth.implicit = true;
    await openRow();
    expect(CARD_BG).toBe('var(--color-glass-6)');
    expect(CARD_BORDER).toBe('1px solid rgba(255,255,255,0.08)');
    expect(NO_ART_BG).toBe('rgba(255,255,255,0.04)');
    expect(SIGN_IN_BUTTON).toEqual({ background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)' });
    const card = screen.getByText('Some.Show.S01.1080p.WEB-GRP').closest('[style*="border-radius: 18px"]') as HTMLElement;
    expect(card.style.background).toBe('var(--color-glass-6)');
    expect(card.style.borderTop).toBe('1px solid rgba(255, 255, 255, 0.08)');
    const tile = screen.getByText('No art').parentElement as HTMLElement;
    expect(tile.style.background).toBe('rgba(255, 255, 255, 0.04)');
    const btn = screen.getByTestId('activity-sign-in');
    expect(btn.style.background).toBe('rgba(255, 255, 255, 0.08)');
  });

  it('no near-black hex colors left in Activity', () => {
    for (const f of ['ActivityFeed.tsx', 'ActivitySheet.tsx']) {
      const src = readFileSync(resolve(__dirname, '../src/components/activity', f), 'utf8');
      expect(src, f).not.toMatch(/#111|#1f1f|#222|#000(?![0-9a-f])/i);
    }
  });
});

describe('Activity: a grab whose path is not mapped', () => {
  it('shows Path not mapped instead of failing silently', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes('/api/downloads/queue')) return res({ downloads: [{ ...QUEUE.downloads[0], progress: 1, status: 'not_mapped', size_remaining: 0 }] });
      if (u.includes('/api/downloads/history')) return res({ entries: [] });
      return res({});
    }));
    render(<MemoryRouter><ActivityFeed /></MemoryRouter>);
    expect(await screen.findByText(PATH_NOT_MAPPED)).toBeTruthy();
    expect(PATH_NOT_MAPPED).toBe('Path not mapped');
  });
});

describe('Activity: a grab with no writable destination (v169, Reviewer + PM)', () => {
  it("shows 'No writable destination' on the item itself, amber, with no helper text", async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes('/api/downloads/queue')) return res({ downloads: [{ ...QUEUE.downloads[0], progress: 1, status: 'no_writable', size_remaining: 0 }] });
      if (u.includes('/api/downloads/history')) return res({ entries: [] });
      return res({});
    }));
    render(<MemoryRouter><ActivityFeed /></MemoryRouter>);
    const label = await screen.findByText(NO_WRITABLE_DEST);
    expect(NO_WRITABLE_DEST).toBe('No writable destination');
    expect(label.getAttribute('data-testid')).toBe('download-status');
    expect(label.textContent).toBe('No writable destination');           // the label alone: no helper text
    expect(label.style.color).toBe('rgb(251, 191, 36)');                  // #fbbf24, the stalled/not-mapped amber
    expect(screen.queryByText(/Settings|choose|writable folder|permission/i)).toBeNull();
  });
});
