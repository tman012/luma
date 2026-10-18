/**
 * v176 Designer polish: phone cards with two actions get one row of two equal-width glass buttons above a
 * right-aligned Reset and Save row (single-action cards and desktop unchanged); Security has its page title
 * and the panel column; no instructional placeholders; Movie and TV profile are selects of Radarr/Sonarr
 * profiles that keep an unlisted saved value and survive an unreachable service.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '@/contexts/AuthContext';
import { SettingsProvider } from '@/pages/settings/SettingsContext';
import { MovieNightCard } from '@/pages/settings/MovieNightCard';
import { GenreRouletteCard } from '@/pages/settings/GenreRouletteCard';
import { FranchiseCompleterCard } from '@/pages/settings/FranchiseCompleterCard';
import { DownloadsGuardCard } from '@/pages/settings/DownloadsGuardCard';
import { SecurityTab } from '@/pages/settings/SecurityTab';

const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
type Profiles = 'ok' | 'down' | 'throw';
function stub(profiles: Profiles = 'ok', portal: Record<string, unknown> = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) return res({ authenticated: true, user: { id: 1, name: 'o', is_admin: true } });
    if (u.includes('/api/security')) return res({ auth_required: 'off', has_pin: false, trusted_proxies: '', hostnames: '', allow_signup: false });
    if (u.includes('/api/request/admin/settings')) return res({ enabled: true, has_password: true, hostname: '', movie_profile: '', tv_profile: '', exclude_libraries: '', ...portal });
    if (u.includes('/api/request/admin/log')) return res({ entries: [] });
    if (u.endsWith('/api/settings')) return res({ automation: { movie_night: { enabled: true }, genre_roulette: { enabled: false },
      franchise_completer: { enabled: true }, downloads_guard: { enabled: true } } });
    if (u.includes('quality-profiles')) {
      if (profiles === 'throw') throw new TypeError('network');
      if (profiles === 'down') return res({ error: 'Radarr not configured' }, 400);
      return res(u.includes('radarr') ? [{ id: 1, name: 'HD-1080p' }, { id: 2, name: 'Ultra-HD' }] : [{ id: 7, name: 'TFlix' }, { id: 8, name: 'Any' }]);
    }
    if (u.includes('canon')) return res({ total: 1000, owned: 700, missing: 300, missing_english: 120 });
    if (u.includes('/status')) return res({ status: { running: false, last_run: null }, log: [], running: false });
    if (u.includes('/trackers') || u.includes('/genres')) return res({ selectable: false, trackers: [], genres: [] });
    return res({});
  }));
}
const media = (phone: boolean) => vi.stubGlobal('matchMedia', vi.fn((q: string) => ({
  matches: phone && q === '(max-width: 599px)', media: q, addEventListener: () => {}, removeEventListener: () => {},
})));
const wrap = (el: React.ReactNode) => render(<MemoryRouter><AuthProvider><SettingsProvider>{el}</SettingsProvider></AuthProvider></MemoryRouter>);

describe('v176 two-action phone footer', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it.each([
    ['Franchise completer', () => <FranchiseCompleterCard />, 'Preview gaps'],
    ['Genre roulette', () => <GenreRouletteCard />, 'Preview spin'],
    ['Movie night', () => <MovieNightCard />, 'Preview pick'],
  ] as const)('%s on a phone: one row of two equal-width glass buttons, then Reset and Save right-aligned', async (_t, el, preview) => {
    stub(); media(true);
    wrap(el());
    const row = await screen.findByTestId('settings-card-footer-actions');
    expect(row.className).toContain('grid-cols-2');
    expect(row.className).toContain('[&>div>button]:w-full');
    const btns = within(row).getAllByRole('button');
    expect(btns.map(b => b.textContent)).toEqual([preview, 'Run now']);
    for (const b of btns) expect(b.className).toContain('bg-white/[0.08]');
    const actions = screen.getByTestId('settings-card-actions');
    expect(actions.className).toContain('justify-end');
    expect(within(actions).getAllByRole('button').map(b => b.textContent)).toEqual(['Reset', 'Save']);
    expect(row.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Run now' })).toHaveLength(1);   // not also in the body
    expect(screen.queryByTestId('settings-card-footer-action')).toBeNull();
  });

  it('Run now from the phone footer still asks before a real run', async () => {
    stub(); media(true);
    wrap(<FranchiseCompleterCard />);
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }));
    expect(await screen.findByTestId('real-run-confirm')).toBeTruthy();
  });

  it('a single-action card keeps the one-row layout on a phone', async () => {
    stub(); media(true);
    wrap(<DownloadsGuardCard />);
    const actions = await screen.findByTestId('settings-card-actions');
    expect(screen.queryByTestId('settings-card-footer-actions')).toBeNull();
    const names = within(actions).getAllByRole('button').map(b => b.textContent);
    expect(names.slice(-2)).toEqual(['Reset', 'Save']);
    expect(within(actions).getByTestId('settings-card-footer-action')).toBeTruthy();
  });

  it('desktop is unchanged: Run now in the body, no two-action row', async () => {
    stub(); media(false);
    wrap(<FranchiseCompleterCard />);
    await screen.findByTestId('settings-card-header');
    expect(screen.queryByTestId('settings-card-footer-actions')).toBeNull();
    const run = screen.getByRole('button', { name: 'Run now' });
    expect(screen.getByTestId('settings-card-actions').contains(run)).toBe(false);
    expect(within(screen.getByTestId('settings-card-actions')).getAllByRole('button').map(b => b.textContent)).toEqual(['Reset', 'Save']);
  });
});

describe('v176 Security panel', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('has the Security page title and the same column as the other panels', async () => {
    stub();
    wrap(<SecurityTab />);
    const tab = await screen.findByTestId('security-tab');
    expect(within(tab).getByRole('heading', { level: 2, name: 'Security' })).toBeTruthy();
    expect(tab.className).toBe('space-y-6');                    // like Rooms, Network, AI: no extra padding
    expect(tab.className).not.toMatch(/\bp-4\b|md:p-6/);
  });

  it('has none of the instructional placeholders', () => {
    const src = readFileSync(join(__dirname, '..', 'src', 'pages', 'settings', 'SecurityTab.tsx'), 'utf8');
    for (const p of ['Leave blank to keep current', 'Confirm new password', 'Radarr quality profile name',
      'Sonarr quality profile name', 'Library names, comma separated']) expect(src).not.toContain(p);
  });

  it('Movie and TV profile are selects of the Radarr and Sonarr profiles', async () => {
    stub('ok', { movie_profile: 'HD-1080p', tv_profile: 'TFlix' });
    wrap(<SecurityTab />);
    const movie = await screen.findByRole('combobox', { name: 'Movie profile' }) as HTMLSelectElement;
    const tv = screen.getByRole('combobox', { name: 'TV profile' }) as HTMLSelectElement;
    await waitFor(() => expect([...movie.options].map(o => o.value)).toEqual(['', 'HD-1080p', 'Ultra-HD']));
    await waitFor(() => expect([...tv.options].map(o => o.value)).toEqual(['', 'TFlix', 'Any']));
    expect(movie.value).toBe('HD-1080p');
    expect(tv.value).toBe('TFlix');
    fireEvent.change(movie, { target: { value: 'Ultra-HD' } });
    expect(movie.value).toBe('Ultra-HD');
  });

  it('keeps a saved profile that the list does not have', async () => {
    stub('ok', { movie_profile: 'Old 4K', tv_profile: '' });
    wrap(<SecurityTab />);
    const movie = await screen.findByRole('combobox', { name: 'Movie profile' }) as HTMLSelectElement;
    await waitFor(() => expect(movie.value).toBe('Old 4K'));
    await waitFor(() => expect([...movie.options].map(o => o.value)).toEqual(['', 'Old 4K', 'HD-1080p', 'Ultra-HD']));
  });

  it.each(['down', 'throw'] as const)('an unreachable Radarr/Sonarr (%s) still shows the saved value', async (mode) => {
    stub(mode, { movie_profile: 'HD-1080p', tv_profile: 'TFlix' });
    wrap(<SecurityTab />);
    const movie = await screen.findByRole('combobox', { name: 'Movie profile' }) as HTMLSelectElement;
    const tv = screen.getByRole('combobox', { name: 'TV profile' }) as HTMLSelectElement;
    await waitFor(() => expect(movie.value).toBe('HD-1080p'));
    expect(tv.value).toBe('TFlix');
    expect(screen.getByText('Request portal')).toBeTruthy();
  });
});
