/** v269 PM: ActivityFeed matches prod 1b4ea74 (section uppercase, #111112 cards,
 * progress bar, Manual/Auto chip placement). All network mocked — no qBit. */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn() }) }));

import { ActivityFeed } from '@/components/activity/ActivityFeed';

const H = 'a'.repeat(40);
const SRC = resolve(__dirname, '../src/components/activity/ActivityFeed.tsx');
const feedSrc = () => readFileSync(SRC, 'utf8');

const res = (body: unknown, status = 200) =>
  ({ ok: status < 300, status, statusText: '', json: async () => body }) as Response;

const calls: string[] = [];
beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes('/api/downloads/remove/')) {
      calls.push(`${init?.method || 'GET'} ${u.slice(u.indexOf('/api/'))}`);
      return res({ success: true, deleteFiles: true });
    }
    if (u.includes('/api/downloads/queue')) {
      return res({
        downloads: [{
          name: 'Some.Show.S01.1080p.WEB-GRP', hash: H, progress: 0.42, status: 'downloading',
          size_total: 10 * 1024 ** 3, size_remaining: 5 * 1024 ** 3, speed: 2_000_000,
          media_type: 'tv', tracker: 'IPT', eta: '12m', title: 'Some Show', year: 2024,
          tmdb_id: 99, poster_path: null,
        }],
      });
    }
    if (u.includes('/api/downloads/history')) {
      return res({
        history: [
          {
            tmdb_title: 'Example Movie', tmdb_year: 2024, media_type: 'movie',
            completed_timestamp: 1_700_000_000, tmdb_id: 42, tracker: 'IPT',
            origin_label: 'Manual', torrent_name: 'Example.Movie.2024.1080p', poster_path: null,
          },
          {
            tmdb_title: 'Roulette Pick', tmdb_year: 2023, media_type: 'movie',
            completed_timestamp: 1_700_100_000, tmdb_id: 43, tracker: null,
            origin_label: 'Genre Roulette', torrent_name: 'Roulette.Pick.2023', poster_path: null,
          },
          {
            tmdb_title: 'Auto Film', tmdb_year: 2022, media_type: 'movie',
            completed_timestamp: 1_700_200_000, tmdb_id: 44, tracker: null,
            origin_label: 'Auto', torrent_name: 'Auto.Film.2022', poster_path: null,
          },
        ],
      });
    }
    return res({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('v269 ActivityFeed matches prod 1b4ea74', () => {
  it('section headers use uppercase tracking-wide (not sentence-case alone)', () => {
    const t = feedSrc();
    expect(t).toMatch(/uppercase tracking-wide text-white\/50">\s*Downloading now/);
    expect(t).toMatch(/uppercase tracking-wide text-white\/50">\s*Completed/);
    // No test navy-glass card tokens
    expect(t).not.toContain("T.glass");
    expect(t).not.toContain('SIGN_IN_TO_DELETE');
    expect(t).toContain("background: '#111112'");
  });

  it('progress bar is prod height 5px with 11px status line; Genre Roulette violet chip', () => {
    const t = feedSrc();
    expect(t).toContain('h-[5px] w-full overflow-hidden rounded-full bg-white/10');
    expect(t).toMatch(/text-\[11px\] text-white\/35/);
    expect(t).toMatch(/167,\s*139,\s*250/);
    expect(t).toContain("badge === 'Genre Roulette'");
    // Origin chip beside title (uppercase), not a meta-line Manual/Auto pair with tracker chip
    expect(t).toMatch(/origin badge[\s\S]*uppercase tracking-wide bg-white\/\[0\.06\]/i);
  });

  it('renders Downloading now / Completed, Manual + Auto chips, and #111112 cards', async () => {
    render(<MemoryRouter><ActivityFeed /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/Downloading now/)).toBeTruthy());
    expect(screen.getByText(/Completed \(3\)/)).toBeTruthy();
    expect(screen.getByText('Manual')).toBeTruthy();
    expect(screen.getByText('Auto')).toBeTruthy();
    expect(screen.getByText('Genre Roulette')).toBeTruthy();
    // Solid prod card, not glass token
    const cards = document.querySelectorAll('[style*="background"]');
    const solid = [...cards].some((el) => (el as HTMLElement).style.background.includes('rgb(17, 17, 18)')
      || (el as HTMLElement).getAttribute('style')?.includes('#111112'));
    expect(solid).toBe(true);
  });

  it('delete torrent + files still works (prod path; no Sign in to delete)', async () => {
    render(<MemoryRouter><ActivityFeed /></MemoryRouter>);
    const name = await screen.findByText('Some.Show.S01.1080p.WEB-GRP');
    await act(async () => { fireEvent.click(name); });
    expect(screen.queryByText('Sign in to delete files')).toBeNull();
    await act(async () => { fireEvent.click(screen.getByText('Delete torrent + files')); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete torrent + files' })); });
    await waitFor(() => expect(calls).toEqual([`DELETE /api/downloads/remove/${H}?deleteFiles=true`]));
  });
});
