/**
 * v174 (Designer, legacy-settings-modal-fix.md): the legacy Settings restyle is look only.
 * 'Run now (downloads for real)' is now a glass secondary 'Run now' whose behavior is exactly as
 * before (one POST with dry_run false, no prompt added or removed); Downloads guard's confirmation
 * step still fires before anything is deleted. Navy surfaces, blue primary, no black, no helper text.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '@/contexts/AuthContext';
import { SettingsProvider } from '@/pages/settings/SettingsContext';
import { MovieNightCard } from '@/pages/settings/MovieNightCard';
import { GenreRouletteCard } from '@/pages/settings/GenreRouletteCard';
import { FranchiseCompleterCard } from '@/pages/settings/FranchiseCompleterCard';
import { DownloadsGuardCard } from '@/pages/settings/DownloadsGuardCard';
import { SettingsCard } from '@/pages/settings/components/SettingsCard';
import { FormField } from '@/pages/settings/components/FormField';
import { ToggleSwitch } from '@/pages/settings/components/ToggleSwitch';
import { PRIMARY_BTN, SECONDARY_BTN, INPUT_CLASS } from '@/pages/settings/components/legacyTokens';
import { SETTINGS_SCRIM, SETTINGS_SHELL, SETTINGS_SHELL_BORDER } from '@/components/layout/PageModal';

const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let posts: { url: string; body: unknown }[] = [];

function stubApi() {
  posts = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (init?.method === 'POST') posts.push({ url: u, body: init.body ? JSON.parse(String(init.body)) : null });
    if (u.includes('/api/auth/me')) return res({ authenticated: true, user: { id: 1, name: 'o', is_admin: true } });
    if (u.endsWith('/api/settings')) return res({ automation: { movie_night: { enabled: true }, genre_roulette: { enabled: true },
      franchise_completer: { enabled: true }, downloads_guard: { enabled: true, trackers: [] } } });
    if (u.includes('/api/settings/schema')) return res({});
    if (u.includes('quality-profiles')) return res([]);
    if (u.includes('/trackers') || u.includes('/genres')) return res({ selectable: false, trackers: [], genres: [] });
    if (u.includes('canon')) return res({ total: 0, owned: 0, missing: 0 });
    if (u.includes('/status')) return res({ status: { running: false, last_run: null }, log: [], running: false });
    if (u.includes('/api/downloads-guard/trackers')) return res({ available: true, trackers: [], unclassified_count: 0, unclassified_bytes: 0, unknown_hosts: [] });
    if (u.includes('/api/downloads-guard/log')) return res({ entries: [] });
    if (u.includes('/run')) return res({ ok: true, dry_run: false, triggered: false });
    return res({});
  }));
}

const wrap = (el: React.ReactNode) => render(<MemoryRouter><AuthProvider><SettingsProvider>{el}</SettingsProvider></AuthProvider></MemoryRouter>);

describe('Run now: look changed, behavior exactly as before', () => {
  beforeEach(() => { localStorage.clear(); stubApi(); vi.stubGlobal('confirm', vi.fn(() => true)); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it.each([
    ['Movie night', <MovieNightCard />, '/api/movie-night/run'],
    ['Genre roulette', <GenreRouletteCard />, '/api/genre-roulette/run'],
    ['Franchise completer', <FranchiseCompleterCard />, '/api/franchise-completer/run'],
  ])('%s: "Run now" is a glass secondary button and runs for real in one click, as before', async (_n, el, path) => {
    wrap(el);
    const btn = await screen.findByRole('button', { name: 'Run now' });
    expect(btn.className).toBe(SECONDARY_BTN);
    expect(btn.className).not.toMatch(/amber|emerald|green/);
    expect(document.body.textContent).not.toContain('downloads for real');
    fireEvent.click(btn);
    await waitFor(() => expect(posts.filter(p => p.url.includes(path))).toHaveLength(1));
    expect(posts.find(p => p.url.includes(path))!.body).toEqual({ dry_run: false });
    expect(window.confirm).not.toHaveBeenCalled();          // there was no prompt before; none was added
  });

  it('Downloads guard: the confirmation step still fires before anything is deleted', async () => {
    wrap(<DownloadsGuardCard />);
    const clean = await screen.findByRole('button', { name: 'Clean up now' });
    expect(clean.className).toBe(SECONDARY_BTN);
    fireEvent.click(clean);
    expect(await screen.findByText('This cannot be undone.')).toBeTruthy();
    expect(posts.filter(p => p.url.includes('/api/downloads-guard/run'))).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(posts.filter(p => p.url.includes('/api/downloads-guard/run'))).toHaveLength(0);
    fireEvent.click(await screen.findByRole('button', { name: 'Clean up now' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Yes — delete permanently' }));
    await waitFor(() => expect(posts.filter(p => p.url.includes('/api/downloads-guard/run'))).toHaveLength(1));
    expect(posts.find(p => p.url.includes('/api/downloads-guard/run'))!.body).toEqual({ dry_run: false });
  });
});

describe('shared pieces: tokens, no helper text', () => {
  beforeEach(() => { localStorage.clear(); stubApi(); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('a card shows its title and fields, never a subtitle, note or hint; Save is blue, Reset glass', async () => {
    wrap(
      <SettingsCard title="Quality" subtitle="SUBTITLE TEXT" note="NOTE TEXT" paths={['a.b']}>
        <FormField label="Max age (hours)" path="a.b" hint="HINT TEXT" />
        <ToggleSwitch label="Auto-grab" path="a.c" hint="SUBLABEL TEXT" />
      </SettingsCard>,
    );
    await screen.findByText('Quality');
    const text = document.body.textContent || '';
    for (const t of ['SUBTITLE TEXT', 'NOTE TEXT', 'HINT TEXT', 'SUBLABEL TEXT']) expect(text).not.toContain(t);
    expect(screen.getByRole('button', { name: 'Save' }).className).toBe(PRIMARY_BTN);
    expect(screen.getByRole('button', { name: 'Reset' }).className).toBe(SECONDARY_BTN);
    expect(screen.getByLabelText('Max age (hours)')).toBeTruthy();
    const sw = screen.getByRole('switch', { name: 'Auto-grab' });
    expect(sw.className).toMatch(/bg-white\/20|bg-\[#3b82f6\]/);
  });

  it('tokens match the spec', () => {
    expect(PRIMARY_BTN).toContain('bg-[#3b82f6]');
    expect(PRIMARY_BTN).toContain('hover:bg-[#60a5fa]');
    expect(PRIMARY_BTN).toContain('disabled:opacity-40');
    expect(SECONDARY_BTN).toContain('bg-white/[0.08]');
    expect(SECONDARY_BTN).toContain('border-white/[0.12]');
    expect(SECONDARY_BTN).toContain('text-white/90');
    expect(INPUT_CLASS).toContain('bg-white/[0.06]');
    expect(INPUT_CLASS).toContain('border-white/[0.12]');
    expect(INPUT_CLASS).toContain('focus:border-[#3b82f6]');
    expect(INPUT_CLASS).toContain('rgba(59,130,246,0.25)');
    expect(SETTINGS_SCRIM).toBe('rgba(21,16,42,0.72)');
    expect(SETTINGS_SHELL).toBe('rgba(21,16,42,0.92)');
    expect(SETTINGS_SHELL_BORDER).toBe('1px solid rgba(255,255,255,0.08)');
  });
});

const SRC = join(__dirname, '..', 'src');
function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p));
    else if (/\.tsx?$/.test(name)) out.push(p);
  }
  return out;
}
const LEGACY = [...files(join(SRC, 'pages/settings')), join(SRC, 'pages/SettingsPage.tsx')];

describe('legacy Settings sources', () => {
  it('no black or charcoal', () => {
    for (const f of LEGACY) {
      const t = readFileSync(f, 'utf8');
      expect([f, t.match(/#000\b|#000000|#0a0a|#111|#1a1a|#222|bg-black|charcoal/gi)]).toEqual([f, null]);
    }
  });
  it('no helper props or picker sublabels are passed', () => {
    for (const f of LEGACY.filter(p => !p.includes('/components/'))) {
      const t = readFileSync(f, 'utf8');
      expect([f, t.match(/\b(hint|subtitle|note|emptyHint|selectedHint)=/g)]).toEqual([f, null]);
      expect([f, t.match(/\n\s*subtitle: '/g)]).toEqual([f, null]);
    }
  });
  it('no green or amber except state (outcomes, test results, tunnel/guard state)', () => {
    const allowed = /OUTCOME_STYLES|grabbed:|failed:|freed:|partial:|found:|ts\.ok|msg\.ok|status\.running|breached|last_error|local_request|preview\.error|phase === 'fallback'|foundAny|ev\.triggered|unreachable|\? 'bg-emerald-500\/20 text-emerald-400'|\? 'bg-amber-500\/20 text-amber-400'|text-amber-300\/50|text-amber-300\/70|text-amber-200\/70|bg-amber-300\/25|bg-emerald-400\/60/;
    const bad: string[] = [];
    for (const f of LEGACY) {
      readFileSync(f, 'utf8').split('\n').forEach((l, i) => {
        if (/(emerald|green|amber|teal|lime)-\d/.test(l) && !allowed.test(l) && !/Toast|TestButton/.test(f)) bad.push(`${f.split('/src/')[1]}:${i + 1}: ${l.trim().slice(0, 100)}`);
      });
    }
    expect(bad).toEqual([]);
  });
  it('the Services group label is gone; group labels are 12px white 50%', () => {
    const t = readFileSync(join(SRC, 'pages/SettingsPage.tsx'), 'utf8');
    expect(t).toContain("group.tabs[0]?.label !== group.section");
    expect(t).toContain('text-[12px] font-medium text-white/50');
    expect(t).toContain('LEGACY.activeNav');
  });
  it('12px minimum', () => {
    for (const f of LEGACY) expect([f, readFileSync(f, 'utf8').match(/text-\[(9|10|11)px\]/g)]).toEqual([f, null]);
  });
  it('the Downloads progress line is 12px', () => {
    const t = readFileSync(join(SRC, 'components/activity/ActivityFeed.tsx'), 'utf8');
    expect(t).toMatch(/data-testid="download-progress-line" className="[^"]*text-\[12px\]/);
  });
});
