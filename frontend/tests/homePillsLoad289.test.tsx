import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

class FakeAudioContext {
  state = 'running';
  resume() { return Promise.resolve(); }
  createBuffer() { return {}; }
  createBufferSource() { return { connect() {}, start() {}, buffer: null }; }
  destination = {};
}
(globalThis as unknown as { AudioContext: unknown }).AudioContext ??= FakeAudioContext;

const auth = {
  authenticated: true, loading: false, needsStart: false, startInProgress: false, pinUnlock: false,
  user: { id: 1, name: 'taylor', is_admin: true }, implicit: false, logout: vi.fn(),
};
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('@/contexts/ModalHostContext', () => ({ useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn() }) }));

import { MinimalHomePage } from '@/pages/MinimalHomePage';
import { SkeletonRails } from '@/components/home/Rails';

const HOME = {
  plex: true,
  continue_watching: [{ key: '10', type: 'episode', title: 'Severance', subtitle: 'S1 E2', poster: null, art: null, progress: 0.25 }],
  rails: [
    { id: '1', title: 'Movies', type: 'movie', items: [{ key: '21', type: 'movie', title: 'Heat', subtitle: '1995', poster: null }] },
  ],
};
const json = (body: unknown, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => body }) as Response;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('v289 phone Home pills load slot (no top→under-CW jump)', () => {
  it('startup shows the logo screen and does not paint pills or skeletons while /api/home is pending', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    expect(screen.getByTestId('startup-loading').textContent).toBe('');
    expect(screen.queryByTestId('home-skeleton')).toBeNull();
    expect(screen.queryByTestId('home-pills')).toBeNull();
  });

  it('after load, pills stay under Continue Watching (same slot)', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).includes('/api/home')) return json(HOME);
      if (String(url).includes('/api/storage/downloads')) {
        return json({ path: '/downloads', available: true, total_bytes: 1, used_bytes: 0, free_bytes: 1,
          cleanup_target_bytes: 1, min_age_hours: 48 });
      }
      return json({});
    }));
    render(<MemoryRouter><MinimalHomePage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByTestId('home-rails')).toBeTruthy());
    const rails = screen.getByTestId('home-rails');
    const pills = screen.getByTestId('home-pills');
    const cw = screen.getByTestId('rail-continue');
    expect(rails.contains(pills)).toBe(true);
    expect(cw.compareDocumentPosition(pills) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('SkeletonRails places afterFirst after the first section only', () => {
    render(
      <SkeletonRails afterFirst={<div data-testid="slot" />} />,
    );
    const sk = screen.getByTestId('home-skeleton');
    const kids = Array.from(sk.children);
    expect(kids[0]!.tagName).toBe('SECTION');
    expect(kids[1]!.getAttribute('data-testid')).toBe('slot');
    expect(kids.filter(k => k.tagName === 'SECTION')).toHaveLength(3);
  });

  it('source: startup is the loading screen; loaded Home still puts pills after the first rail', () => {
    const home = readFileSync(resolve(__dirname, '../src/pages/MinimalHomePage.tsx'), 'utf8');
    expect(home).toContain('<StartupLoading />');
    expect(home).toContain('afterFirst={<HomePills after />}');
    expect(home).not.toMatch(/<>\s*<HomePills\s*\/>\s*<SkeletonRails\s*\/>\s*<\/>/);
  });
});
