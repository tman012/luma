/** v267 Activity: prod's Home Activity button + ActivitySheet. All network mocked (no qBit). */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { readFileSync } from 'node:fs';

vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ implicit: false, authenticated: true }) }));
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn() }) }));

import { HomePills, HOME_PILLS } from '@/components/home/HomePills';

const GIB = 1024 ** 3;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function stub(history: unknown[] = [], downloads: unknown[] = []) {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || 'GET';
    calls.push({ url: String(url), method });
    const u = String(url);
    if (u.includes('/api/storage/downloads')) {
      return json({ path: '/downloads', available: true, total_bytes: 4000 * GIB, used_bytes: 3588 * GIB, free_bytes: 412 * GIB,
        cleanup_target_bytes: 100 * GIB, min_age_hours: 48 });
    }
    if (u.includes('/api/downloads/queue')) return json({ downloads });
    if (u.includes('/api/downloads/history')) return json({ history });
    if (u.includes('/api/downloads/remove/')) return json({ success: true });
    return json({});
  }));
  return calls;
}

describe('v267 Activity button and sheet (prod 1b4ea74)', () => {
  it('Home rail has Activity in prod order (after Storage, before TV Calendar / Rooms)', async () => {
    stub();
    render(<MemoryRouter><HomePills /></MemoryRouter>);
    expect(HOME_PILLS.map(p => p.id)).toEqual(['activity', 'tv-calendar', 'rooms']);
    expect(HOME_PILLS[0]!.label).toBe('Activity');
    const btn = screen.getByTestId('pill-activity');
    expect(btn.textContent).toContain('Activity');
    // Prod's activity icon path
    expect(btn.querySelector('path')?.getAttribute('d')).toBe('M22 12h-4l-3 9L9 3l-3 9H2');
  });

  it('opens ActivitySheet titled Activity and loads queue + history from Connectors qBit APIs', async () => {
    const hist = [{
      tmdb_title: 'Example Movie', tmdb_year: 2024, media_type: 'movie',
      completed_timestamp: 1_700_000_000, tmdb_id: 42, tracker: null,
      origin_label: 'Manual', torrent_name: 'Example.Movie.2024.1080p', poster_path: null,
    }];
    const calls = stub(hist, []);
    render(<MemoryRouter><HomePills /></MemoryRouter>);
    fireEvent.click(screen.getByTestId('pill-activity'));
    await waitFor(() => expect(document.body.querySelector('[data-sheet="ActivitySheet"]')).toBeTruthy());
    expect(document.body.querySelector('h2')?.textContent).toBe('Activity');
    await waitFor(() => expect(document.body.textContent).toContain('Example Movie'));
    const urls = calls.map(c => c.url);
    expect(urls.some(u => u.includes('/api/downloads/history'))).toBe(true);
    expect(urls.some(u => u.includes('/api/downloads/queue'))).toBe(true);
    // Never hardcodes a qBit host — only the app APIs that read Connectors settings.
    expect(urls.every(u => !/192\.168\.|qbittorrent|:8080|:8085/i.test(u))).toBe(true);
  });

  it("sheet title and empty copy match prod", () => {
    const sheet = readFileSync('src/components/activity/ActivitySheet.tsx', 'utf8');
    const feed = readFileSync('src/components/activity/ActivityFeed.tsx', 'utf8');
    expect(sheet).toContain('>Activity</h2>');
    expect(feed).toContain('No downloads yet.');
    expect(feed).toContain("Couldn't load activity — tap to retry");
  });
});
