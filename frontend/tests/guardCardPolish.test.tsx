// Tester ab1c841 #5 (full mode): navy glass Settings popup and Notifications popover,
// MB amounts in the guard log, no "— preferred", no helper text on the guard card.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '@/contexts/AuthContext';
import { DownloadsGuardCard, describe as describeRun } from '@/pages/settings/DownloadsGuardCard';
import { SettingsProvider } from '@/pages/settings/SettingsContext';
import { NAVY_GLASS_BORDER, NAVY_GLASS_PANEL, NAVY_GLASS_POPOVER } from '@/theme/tokens';
import type { DownloadsGuardResult } from '@/services/settingsApi';

const MB = 1024 ** 2;
const GB = 1024 ** 3;
const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const run = (over: Partial<DownloadsGuardResult>) => ({
  triggered: true, dry_run: false, freed_bytes: 0, freed_preferred_bytes: 0, freed_fallback_bytes: 0,
  free_after_bytes: 99 * GB, deleted: [{}, {}, {}], failed: [], short: false, reason: '', error: null, ...over,
}) as unknown as DownloadsGuardResult;

describe('guard log wording', () => {
  it('MB amounts read as MB and an empty preferred share has no placeholder', () => {
    const line = describeRun(run({ freed_bytes: 8 * MB, freed_fallback_bytes: 8 * MB, short: true }));
    expect(line).toBe('Freed 8 MB across 3 entries · 8 MB from other trackers · short of target — ran out of eligible entries · 99 GB free now');
    expect(line).not.toContain('—  ');
    expect(line).not.toMatch(/— preferred|0 GB/);
  });
  it('both shares when the chosen trackers gave some', () => {
    expect(describeRun(run({ freed_bytes: 3 * GB, freed_preferred_bytes: 1 * GB, freed_fallback_bytes: 2 * GB })))
      .toBe('Freed 3 GB across 3 entries · 1 GB from chosen trackers + 2 GB other · 99 GB free now');
  });
});

describe('guard card helper text', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes('/api/auth/me')) return res({ authenticated: true, user: { id: 1, name: 'o', is_admin: true } });
      if (u.includes('/api/downloads-guard/trackers')) return res({ available: true, trackers: [], unclassified_count: 0, unclassified_bytes: 0, unknown_hosts: [] });
      if (u.includes('/api/downloads-guard/status')) return res({ running: false });
      if (u.includes('/api/downloads-guard/log')) return res({ entries: [] });
      if (u.endsWith('/api/settings')) return res({ automation: { downloads_guard: { enabled: false, trackers: [] } } });
      return res({});
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('no card note, no field hints, no picker sublabel', async () => {
    render(<MemoryRouter><AuthProvider><SettingsProvider><DownloadsGuardCard /></SettingsProvider></AuthProvider></MemoryRouter>);
    await screen.findByText('Delete from these trackers first');
    await waitFor(() => expect(document.body.textContent).toContain('Downloads guard'));
    const text = document.body.textContent || '';
    for (const helper of ['Watches free space', 'Nothing is deleted until this is on', 'How often to look',
      'Deleting starts below', 'How much to free', 'backstop', 'No preference', 'Spends the oldest',
      'the figure the trigger uses', 'never treated as belonging', 'Preview first', 'Preview is always safe', 'deletes for real']) {
      expect(text).not.toContain(helper);
    }
    fireEvent.click(await screen.findByRole('button', { name: 'Clean up now' }));
    const confirm = document.body.textContent || '';
    expect(confirm).toContain('This cannot be undone.');
    expect(confirm).not.toContain('Preview first');
  });
});

describe('navy glass surfaces', () => {
  const src = (p: string) => readFileSync(`${process.cwd()}/src/${p}`, 'utf8');
  it('tokens are navy (#15102a base), not near-black', () => {
    expect(NAVY_GLASS_PANEL).toContain('var(--color-kiosk-black)');
    expect(NAVY_GLASS_POPOVER).toContain('rgb(21 16 42');
    expect(NAVY_GLASS_POPOVER).toContain('var(--color-glass-6)');
    expect(NAVY_GLASS_BORDER).toBe('1px solid var(--color-glass-12)');
  });
  it('the Settings popup (PageModal) and the Notifications popover use them', () => {
    const modal = src('components/layout/PageModal.tsx');
    expect(modal).not.toContain('#141414');
    expect(modal).toContain('NAVY_GLASS_PANEL');
    const bar = src('components/layout/TopBar.tsx');
    expect(bar).toContain('background: NAVY_GLASS_POPOVER');
    expect(bar).not.toContain('bg-black/95');
  });
});
