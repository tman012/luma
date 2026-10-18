/** PM ruling: deleting a download's files needs the owner signed in with a
 * session, even with LAN sign-in off. Activity offers a sign-in instead. */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const auth = { implicit: false };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn() }) }));

import { ActivityFeed, activeEdge, fmtCompleted, historyEdge, PATH_NOT_MAPPED, NO_WRITABLE_DEST, CARD_BG, CARD_BORDER, NO_ART_BG, SIGN_IN_BUTTON, SIGN_IN_TO_DELETE, removeDownload } from '@/components/activity/ActivityFeed';

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
    expect(screen.queryByText('No art')).toBeNull();                // v170: icon, no text
    const tile = screen.getByTestId('no-art').parentElement as HTMLElement;
    expect(screen.getByTestId('no-art').textContent).toBe('');
    expect(screen.getByTestId('no-art-icon').getAttribute('stroke')).toBe('rgba(255,255,255,0.25)');
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


describe('Downloads polish (v170, Designer)', () => {
  const queue = (rows: Record<string, unknown>[]) => vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const u = String(url);
    if (u.includes('/api/downloads/queue')) return res({ downloads: rows });
    if (u.includes('/api/downloads/history')) return res({ history: [
      { tmdb_title: 'Done Movie', torrent_name: 'Done.Movie.2024', completed_timestamp: 2, success: true, imported: true, tracker: 'IPT' },
      { tmdb_title: 'Waiting Movie', torrent_name: 'Waiting.Movie.2024', completed_timestamp: 1, success: true, imported: false, tracker: 'IPT' },
      { tmdb_title: 'Broken Movie', torrent_name: 'Broken.Movie.2024', completed_timestamp: 0, success: false, imported: false },
    ] });
    return res({});
  }));
  const edge = (text: string) => (screen.getByText(text).closest('[style*="border-radius: 18px"]') as HTMLElement).style.borderLeft;

  it('the row edge follows the state, never the tracker', async () => {
    queue([
      { ...QUEUE.downloads[0], name: 'Going.S01', hash: 'b'.repeat(40), status: 'downloading', tracker: 'IPT' },
      { ...QUEUE.downloads[0], name: 'Stuck.S01', hash: 'c'.repeat(40), status: 'stalled', tracker: 'IPT' },
      { ...QUEUE.downloads[0], name: 'Nowhere.S01', hash: 'd'.repeat(40), status: 'no_writable', tracker: 'BHD' },
    ]);
    render(<MemoryRouter><ActivityFeed /></MemoryRouter>);
    await screen.findByText('Going.S01');
    expect(edge('Going.S01')).toBe('3px solid rgb(59, 130, 246)');
    expect(edge('Stuck.S01')).toBe('3px solid rgb(251, 191, 36)');
    expect(edge('Nowhere.S01')).toBe('3px solid rgb(251, 191, 36)');
    await screen.findByText('Done Movie');
    expect(edge('Done Movie')).toBe('3px solid rgb(34, 197, 94)');
    expect(edge('Waiting Movie')).toBe('3px solid rgba(255, 255, 255, 0.18)');
    expect(edge('Broken Movie')).toBe('3px solid rgb(251, 191, 36)');
    expect(activeEdge('copying')).toBe('rgba(255,255,255,0.18)');     // green only once imported
    expect(historyEdge({ imported: true, failed: false })).toBe('#22c55e');
  });

  it("'No writable destination' and 'Path not mapped' are 12px amber", async () => {
    queue([
      { ...QUEUE.downloads[0], name: 'A.S01', hash: 'e'.repeat(40), status: 'no_writable', progress: 1, size_remaining: 0 },
      { ...QUEUE.downloads[0], name: 'B.S01', hash: 'f'.repeat(40), status: 'not_mapped', progress: 1, size_remaining: 0 },
    ]);
    render(<MemoryRouter><ActivityFeed /></MemoryRouter>);
    for (const label of [NO_WRITABLE_DEST, PATH_NOT_MAPPED]) {
      const el = await screen.findByText(label);
      expect(el.className).toContain('text-[12px]');
      expect(el.style.color).toBe('rgb(251, 191, 36)');
      expect(el.className).not.toContain('truncate');                 // never clipped
    }
  });

  it('section headers are sentence case, not all caps', async () => {
    queue([{ ...QUEUE.downloads[0], name: 'X.S01', hash: '1'.repeat(40) }]);
    render(<MemoryRouter><ActivityFeed /></MemoryRouter>);
    await screen.findByText('X.S01');
    await screen.findByText('Done Movie');
    const heads = screen.getAllByTestId('section-header');
    expect(heads.map(h => h.textContent!.trim())).toEqual(['Downloading now (1)', 'Completed (3)']);
    for (const h of heads) expect(h.className).not.toMatch(/uppercase/);
    const src = readFileSync(resolve(__dirname, '../src/components/activity/ActivityFeed.tsx'), 'utf8');
    expect(src).not.toMatch(/DOWNLOADING NOW|No art/);
  });
});


describe('Completed rows (v171, Designer)', () => {
  const NOW = new Date(2026, 9, 18, 9, 0).getTime();
  const at = (y: number, mo: number, d: number, h: number, mi: number) => new Date(y, mo, d, h, mi).getTime() / 1000;
  const history = () => vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const u = String(url);
    if (u.includes('/api/downloads/queue')) return res({ downloads: [
      { ...QUEUE.downloads[0], name: 'Live.S01', hash: '2'.repeat(40), tracker: 'IPTorrents' }] });
    if (u.includes('/api/downloads/history')) return res({ history: [
      { tmdb_title: 'Heat', torrent_name: 'Heat.1995', completed_timestamp: at(2026, 9, 17, 4, 20), success: true, imported: true,
        tracker: 'IPTorrents', origin_label: 'Manual' },
      { tmdb_title: 'Ronin', torrent_name: 'Ronin.1998', completed_timestamp: at(2025, 11, 31, 23, 5), success: true, imported: true,
        tracker: null, origin_label: 'Manual' },
    ] });
    return res({});
  }));

  it("the date reads 'Oct 17, 4:20 AM': no leading zeros, the year only when it isn't this year", () => {
    expect(fmtCompleted(at(2026, 9, 17, 4, 20) * 1000, NOW)).toBe('Oct 17, 4:20 AM');
    expect(fmtCompleted(at(2026, 2, 5, 0, 7) * 1000, NOW)).toBe('Mar 5, 12:07 AM');
    expect(fmtCompleted(at(2026, 9, 1, 12, 0) * 1000, NOW)).toBe('Oct 1, 12:00 PM');
    expect(fmtCompleted(at(2025, 11, 31, 23, 5) * 1000, NOW)).toBe('Dec 31, 2025, 11:05 PM');
  });

  it("the tracker is the active rows' chip, the origin a neutral 'Manual' chip, no all caps", async () => {
    history();
    render(<MemoryRouter><ActivityFeed /></MemoryRouter>);
    await screen.findByText('Heat');
    const live = screen.getByText('Live.S01').closest('[style*="border-radius: 18px"]') as HTMLElement;
    const liveChip = within(live).getByText('IPTorrents');
    const trackers = screen.getAllByTestId('history-tracker');
    expect(trackers.map(t => t.textContent)).toEqual(['IPTorrents']);              // Ronin has no tracker: no chip
    expect(trackers[0].className).toBe(liveChip.className.replace(/\s+$/, '') + ' whitespace-nowrap');
    expect(trackers[0].getAttribute('style')).toBe(liveChip.getAttribute('style'));   // same tracker color
    const origins = screen.getAllByTestId('history-origin');
    expect(origins.map(o => o.textContent)).toEqual(['Manual', 'Manual']);
    expect(origins[0].style.background).toBe('rgba(255, 255, 255, 0.06)');            // neutral
    for (const el of [...trackers, ...origins, ...screen.getAllByTestId('history-date')]) {
      expect(el.className).not.toMatch(/uppercase|tracking-wide/);
      expect(el.textContent).not.toBe(el.textContent!.toUpperCase());
    }
    const src = readFileSync(resolve(__dirname, '../src/components/activity/ActivityFeed.tsx'), 'utf8');
    expect(src).not.toMatch(/\buppercase\b|toUpperCase|MANUAL/);
    expect(screen.getAllByTestId('history-date').map(d => d.textContent)[0]).toMatch(/^Oct 17, 4:20 AM$/);
  });
});
