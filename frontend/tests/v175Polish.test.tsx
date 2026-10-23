/**
 * v175 Designer polish and must-check guards: chips at 12px, consistent feature names, header switches
 * instead of a duplicate title row, "Run time", the 2x2 Movie night tiles on phones, "Rooms & devices",
 * "Couldn't load …" with no doubled prefix, the 13px / white 50% rooms empty state, and a Security tab
 * that never goes blank when its load fails.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '@/contexts/AuthContext';
import { SettingsProvider } from '@/pages/settings/SettingsContext';
import { MovieNightCard } from '@/pages/settings/MovieNightCard';
import { GenreRouletteCard } from '@/pages/settings/GenreRouletteCard';
import { FranchiseCompleterCard } from '@/pages/settings/FranchiseCompleterCard';
import { SecurityTab } from '@/pages/settings/SecurityTab';
import { couldntLoad } from '@/utils/messages';

const SRC = join(__dirname, '..', 'src');
function walk(d: string, out: string[] = []): string[] {
  for (const n of readdirSync(d)) {
    const p = join(d, n);
    if (statSync(p).isDirectory()) walk(p, out); else if (/\.(tsx?|css)$/.test(n)) out.push(p);
  }
  return out;
}
const FILES = walk(SRC).map(p => [p.slice(SRC.length + 1), readFileSync(p, 'utf8')] as const);
const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');

describe('v175 static guards', () => {
  it('chips (rounded, py-0.5) are never smaller than 12px', () => {
    const bad: string[] = [];
    for (const [f, s] of FILES) {
      if (/AlphabetSidebar|TopBar/.test(f)) continue;               // letter rail and the nav count badge are not chips
      s.split('\n').forEach((l, i) => {
        if (/rounded/.test(l) && /\bpy-0\.5\b/.test(l) && /text-\[(?:[0-9]|1[01])px\]/.test(l)) bad.push(`${f}:${i + 1}`);
      });
    }
    expect(bad).toEqual([]);
  });

  it('feature names are sentence case everywhere users can see them', () => {
    const bad: string[] = [];
    for (const [f, s] of FILES) {
      const code = stripComments(s);
      // v261 PM ruling: the v260 Downloads Guard card is prod 1b4ea74's, so its title matches prod exactly
      const prodPorted = f.endsWith('components/automation/DownloadsGuardSettings.tsx');
      for (const m of code.matchAll(/Movie Night|Genre Roulette|Franchise Completer|Downloads Guard|Auto-Grab/g)) {
        if (!(prodPorted && m[0] === 'Downloads Guard')) bad.push(`${f}: ${m[0]}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it('load errors say "Couldn\'t load …" once, with no doubled prefix', () => {
    expect(couldntLoad('rooms')).toBe("Couldn't load rooms");
    const bad: string[] = [];
    for (const [f, s] of FILES) {
      const code = stripComments(s);
      if (/Failed to load [a-z ]+: /.test(code)) bad.push(f);
    }
    expect(bad).toEqual([]);
    const rooms = FILES.find(([f]) => f.endsWith('RoomAccordion.tsx'))![1];
    expect(rooms).toContain("couldntLoad('rooms')");
    expect(rooms).toMatch(/text-\[13px\] text-white\/50[^\n]*>\s*No rooms configured|No rooms configured/);
    const empty = rooms.split('\n').findIndex(l => l.includes('No rooms configured'));
    expect(rooms.split('\n').slice(Math.max(0, empty - 3), empty + 1).join('\n')).toContain('text-[13px] text-white/50');
  });

  it('"Rooms & devices" and "Run time"', () => {
    const tab = FILES.find(([f]) => f.endsWith('RoomsTab.tsx'))![1];
    expect(tab).toContain('Rooms &amp; devices');
    expect(tab).not.toContain('Rooms / devices');
    for (const [f, s] of FILES) expect(`${f}:${s.includes('Run time (24h)')}`).toBe(`${f}:false`);
  });
});

const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
function stub(security: 'ok' | '404' | 'throw') {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) return res({ authenticated: true, user: { id: 1, name: 'o', is_admin: true } });
    if (u.includes('/api/security')) {
      if (security === 'throw') throw new TypeError('network');
      if (security === '404') return res({ error: 'nope' }, 404);
      return res({ auth_required: 'off', has_pin: false, trusted_proxies: '', hostnames: '', allow_signup: false });
    }
    if (u.endsWith('/api/settings')) return res({ automation: { movie_night: { enabled: true }, genre_roulette: { enabled: false },
      franchise_completer: { enabled: true } } });
    if (u.includes('quality-profiles')) return res([]);
    if (u.includes('canon')) return res({ total: 1000, owned: 700, missing: 300, missing_english: 120 });
    if (u.includes('/status')) return res({ status: { running: false, last_run: null }, log: [], running: false });
    if (u.includes('/trackers') || u.includes('/genres')) return res({ selectable: false, trackers: [], genres: [] });
    return res({});
  }));
}
const wrap = (el: React.ReactNode) => render(<MemoryRouter><AuthProvider><SettingsProvider>{el}</SettingsProvider></AuthProvider></MemoryRouter>);

describe('v175 rendered polish', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it.each([
    ['Movie night', () => <MovieNightCard />, 'Preview pick', true],
    ['Genre roulette', () => <GenreRouletteCard />, 'Preview spin', false],
    ['Franchise completer', () => <FranchiseCompleterCard />, 'Preview gaps', true],
  ] as const)('%s: the on/off switch sits in the header beside the preview, with no duplicate row', async (title, el, preview, on) => {
    stub('ok');
    wrap(el());
    const sw = await screen.findByTestId('card-header-switch');
    await waitFor(() => expect(sw.getAttribute('aria-checked')).toBe(String(on)));
    expect(sw.getAttribute('aria-label')).toBe(title);
    expect(screen.getAllByRole('switch', { name: title })).toHaveLength(1);
    const btn = screen.getByRole('button', { name: preview });
    expect(btn.parentElement).toBe(sw.parentElement);                       // same header-right group
    expect(btn.compareDocumentPosition(sw) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); // switch right-aligned, after
    fireEvent.click(sw);
    expect(sw.getAttribute('aria-checked')).toBe(String(!on));
  });

  it('Movie night stat tiles are 2x2 under 600px and their labels never wrap', async () => {
    stub('ok');
    wrap(<MovieNightCard />);
    const grid = await screen.findByTestId('movie-night-stats');
    expect(grid.className).toContain('grid-cols-2');
    expect(grid.className).toContain('min-[600px]:grid-cols-4');
    for (const l of ['Great films', 'In Plex', 'Missing', 'English gaps']) {
      expect(within(grid).getByText(l).className).toContain('whitespace-nowrap');
    }
    expect(screen.getByText('Run time')).toBeTruthy();
  });

  it.each(['404', 'throw'] as const)('Security: a failed load (%s) still shows the portal card and says so, with Try again', async (mode) => {
    stub(mode);
    wrap(<SecurityTab />);
    const err = await screen.findByTestId('security-load-error');
    expect(err.textContent).toContain("Couldn't load security settings");
    expect(screen.getByText('Request portal')).toBeTruthy();
    stub('ok');
    fireEvent.click(within(err).getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(screen.queryByTestId('security-load-error')).toBeNull());
    expect(screen.getAllByText('Require PIN').length).toBeGreaterThan(0);
  });

  it('Security: a good load shows the PIN card with the primary Save', async () => {
    stub('ok');
    wrap(<SecurityTab />);
    await waitFor(() => expect(screen.getAllByText('Require PIN').length).toBeGreaterThan(0));
    for (const b of screen.getAllByRole('button', { name: 'Save' })) expect(b.className).toContain('bg-[#3b82f6]');
  });
});

describe('v175 phone card headers (Designer, under 600px)', () => {
  const media = (phone: boolean) => vi.stubGlobal('matchMedia', vi.fn((q: string) => ({
    matches: phone && q === '(max-width: 599px)', media: q, addEventListener: () => {}, removeEventListener: () => {},
  })));
  beforeEach(() => localStorage.clear());
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it.each([
    ['Movie night', () => <MovieNightCard />, 'Preview pick'],
    ['Genre roulette', () => <GenreRouletteCard />, 'Preview spin'],
    ['Franchise completer', () => <FranchiseCompleterCard />, 'Preview gaps'],
  ] as const)('%s: title left, switch right, no icon; the action sits in the footer above Reset and Save', async (title, el, action) => {
    stub('ok'); media(true);
    wrap(el());
    const header = await screen.findByTestId('settings-card-header');
    expect(header.querySelector('svg')).toBeNull();                         // icon hidden
    expect(within(header).queryByRole('button', { name: action })).toBeNull();
    expect(header.textContent).toBe(title);                                // just the title (and the switch)
    const sw = within(header).getByRole('switch', { name: title });
    expect(within(header).getByRole('heading', { name: title }).compareDocumentPosition(sw) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // v176: these cards have two actions, so the action and Run now share a row above Reset and Save
    const row = screen.getByTestId('settings-card-footer-actions');
    expect(within(row).getAllByRole('button').map(b => b.textContent)).toEqual([action, 'Run now']);
    expect(within(row).getByRole('button', { name: action }).className).toContain('bg-white/[0.08]');   // glass secondary
    const footer = screen.getByTestId('settings-card-actions');
    expect(within(footer).getAllByRole('button').map(b => b.textContent)).toEqual(['Reset', 'Save']);
  });

  it('desktop is unchanged: icon and action in the header', async () => {
    stub('ok'); media(false);
    wrap(<MovieNightCard />);
    const header = await screen.findByTestId('settings-card-header');
    expect(header.querySelector('svg')).not.toBeNull();
    expect(within(header).getByRole('button', { name: 'Preview pick' })).toBeTruthy();
    expect(screen.queryByTestId('settings-card-footer-action')).toBeNull();
  });
});
