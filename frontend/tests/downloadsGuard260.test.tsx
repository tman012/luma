import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DownloadsGuardSettings, SIGN_IN_TO_CHANGE, ageOptions, describe as describeRun } from '@/components/automation/DownloadsGuardSettings';

const GIB = 1024 ** 3;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const DEFAULTS = {
  enabled: false, check_interval_minutes: 15, min_free_gb: 150, reclaim_gb: 100, daily_cap_gb: 500, max_run_gb: 2048,
  trackers: ['ipt'], pill_gb: 100, min_age_days: 2, min_seed_hours: 0, min_ratio: 0, scope: 'all', folder: '',
  protected_tags: ['luma-keep'], protected_hashes: ['7224bfad84076c0a76c36fb891d3b91ac226efb6'],
};
const TRACKERS = [['ipt', 'IPTorrents'], ['beyondhd', 'BeyondHD'], ['morethantv', 'MoreThanTV'], ['privatehd', 'PrivateHD'], ['pixelhd', 'PixelHD']]
  .map(([key, label]) => ({ key, label }));
const VIEW = (settings = DEFAULTS, allowed = true) => ({ settings, defaults: DEFAULTS, folder: '/downloads', media_folder: '/downloads', trackers: TRACKERS, allowed });
const STATUS = {
  running: false, last_check: '2026-10-23T16:30:00-04:00', last_run: null, last_result: null,
  evaluation: { enabled: false, available: true, free_bytes: 412 * GIB, min_free_bytes: 150 * GIB, daily_cap_bytes: 500 * GIB, freed_today_bytes: 0, triggered: false },
};

function stub(handler?: (url: string, init?: RequestInit) => Response | undefined, allowed = true) {
  const calls: { url: string; init?: RequestInit }[] = [];
  let cur = VIEW(DEFAULTS, allowed);
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const h = handler?.(url, init);
    if (h) return h;
    if (init?.method === 'PUT') { cur = VIEW({ ...cur.settings, ...JSON.parse(String(init.body)) }, allowed); return json(cur); }
    if (url.endsWith('/status')) return json({ status: STATUS, log: [{ at: '2026-10-23T16:00:00-04:00', outcome: 'nothing', summary: 'Above the floor' }] });
    if (url.endsWith('/trackers')) return json({ available: true, trackers: [{ key: 'ipt', label: 'IPTorrents', count: 12, size_bytes: 300 * GIB }] });
    if (url.endsWith('/preview')) {
      return json({ target_bytes: 100 * GIB, planned_bytes: 60 * GIB, preferred_bytes: 60 * GIB, fallback_bytes: 0, preferred_available: 2,
        eligible_count: 2, short: true, entries: [{ name: 'Heat.1995', size_bytes: 40 * GIB, tracker_label: 'IPTorrents', phase: 'preferred' },
          { name: 'Zodiac.2007', size_bytes: 20 * GIB, tracker_label: 'BeyondHD', phase: 'fallback' }] });
    }
    if (url.endsWith('/run')) return json({ ok: true, dry_run: false });
    return json(cur);
  }));
  return calls;
}
const puts = (calls: { init?: RequestInit }[]) => calls.filter(c => c.init?.method === 'PUT').map(c => JSON.parse(String(c.init!.body)));
const view = () => render(<MemoryRouter><DownloadsGuardSettings /></MemoryRouter>);

describe('v260 Settings > Automation > Downloads guard', () => {
  it("shows every setting with prod's defaults, sentence case, no helper text", async () => {
    stub();
    view();
    await waitFor(() => screen.getByTestId('downloads-guard'));
    for (const t of ['Downloads guard', 'Enabled', 'Floor (GB)', 'Reclaim per run (GB)', 'Check every (minutes)', 'Storage pill amount (GB)',
      'Daily limit (GB)', 'Per-run limit (GB)', 'Downloads', 'Keep downloads newer than', '2 days', 'Minimum seed time (hours)', 'Minimum ratio',
      'Delete from these trackers first', 'Folder', 'Preview', 'Clean up now', 'Status', 'Free space', 'Recent activity']) {
      expect(screen.getAllByText(t).length, t).toBeGreaterThan(0);
    }
    const val = (id: string) => (screen.getByTestId(`${id}-input`) as HTMLInputElement).value;
    expect([val('dg-floor'), val('dg-reclaim'), val('dg-interval'), val('dg-pill'), val('dg-daily-value'), val('dg-max-run')])
      .toEqual(['150', '100', '15', '100', '500', '2,048']);
    expect(screen.queryByText(/Minimum age/)).toBeNull();
    expect(screen.getByTestId('dg-enabled-toggle').getAttribute('aria-checked')).toBe('false');
    expect(screen.getByTestId('dg-seed-toggle').getAttribute('aria-checked')).toBe('false');
    expect(screen.getByTestId('dg-ratio-toggle').getAttribute('aria-checked')).toBe('false');
    expect(screen.getByText('All finished downloads')).toBeTruthy();
    expect(screen.getAllByTestId('dg-tracker-row').map(r => r.textContent)).toEqual([expect.stringContaining('IPTorrents · 12 · 300 GB')]);
    expect((screen.getByTestId('dg-folder-input') as HTMLInputElement).placeholder).toBe('/downloads');
    expect(document.body.textContent).not.toMatch(/Downloads Guard|default 150|cannot be undone/);
  });

  it('turns on, sets numbers, toggles the daily limit off, and reorders trackers', async () => {
    const calls = stub();
    view();
    await waitFor(() => screen.getByTestId('downloads-guard'));
    fireEvent.click(screen.getByTestId('dg-enabled-toggle'));
    const floor = screen.getByTestId('dg-floor-input');
    fireEvent.change(floor, { target: { value: '200' } });
    fireEvent.blur(floor);
    fireEvent.click(screen.getByTestId('dg-daily-toggle'));
    fireEvent.click(screen.getByTestId('dg-add-beyondhd'));
    await waitFor(() => screen.getByTestId('dg-up-beyondhd'));
    fireEvent.click(screen.getByTestId('dg-up-beyondhd'));
    await waitFor(() => expect(puts(calls)).toEqual([
      { enabled: true }, { min_free_gb: 200 }, { daily_cap_gb: 0 }, { trackers: ['ipt', 'beyondhd'] }, { trackers: ['beyondhd', 'ipt'] }]));
    expect(screen.getAllByTestId('dg-tracker-row').map(r => r.textContent?.slice(0, 2))).toEqual(['1B', '2I']);
    fireEvent.click(screen.getByTestId('dg-remove-ipt'));
    await waitFor(() => expect(puts(calls).at(-1)).toEqual({ trackers: ['beyondhd'] }));
  });

  it('a change the server refuses for the implicit owner asks to sign in and rolls back', async () => {
    stub((u, init) => (init?.method === 'PUT' ? json({ error: 'Sign in', sign_in: true }, 401) : undefined));
    view();
    await waitFor(() => screen.getByTestId('downloads-guard'));
    fireEvent.click(screen.getByTestId('dg-enabled-toggle'));
    expect(await screen.findByText(SIGN_IN_TO_CHANGE)).toBeTruthy();
    expect(screen.getByTestId('dg-enabled-toggle').getAttribute('aria-checked')).toBe('false');
  });

  it('Preview lists what would go; Clean up now asks first, then deletes for real', async () => {
    const calls = stub();
    view();
    await waitFor(() => screen.getByTestId('downloads-guard'));
    fireEvent.click(screen.getByTestId('dg-preview'));
    expect(await screen.findByText('Would delete 60 GB of 100 GB')).toBeTruthy();
    expect(screen.getAllByTestId('dg-preview-list-row').length).toBe(2);
    fireEvent.click(screen.getByTestId('dg-run'));
    expect(calls.some(c => c.url.endsWith('/run'))).toBe(false);
    fireEvent.click(screen.getByTestId('dg-cancel'));
    fireEvent.click(screen.getByTestId('dg-run'));
    fireEvent.click(screen.getByTestId('dg-confirm'));
    await waitFor(() => expect(calls.filter(c => c.url.endsWith('/run')).map(c => JSON.parse(String(c.init!.body)))).toEqual([{ dry_run: false }]));
  });

  it('Clean up now from the implicit owner asks to sign in', async () => {
    stub((u) => (u.endsWith('/run') ? json({ error: 'Sign in', sign_in: true }, 401) : undefined));
    view();
    await waitFor(() => screen.getByTestId('downloads-guard'));
    fireEvent.click(screen.getByTestId('dg-run'));
    fireEvent.click(screen.getByTestId('dg-confirm'));
    expect(await screen.findByTestId('dg-sign-in')).toBeTruthy();
  });

  it('Keep downloads newer than is in days', async () => {
    const calls = stub();
    view();
    await waitFor(() => screen.getByTestId('downloads-guard'));
    fireEvent.click(screen.getByText('2 days'));
    fireEvent.click(await screen.findByText('7 days'));
    await waitFor(() => expect(puts(calls)).toEqual([{ min_age_days: 7 }]));
    expect(ageOptions(45).map(o => o.label)).toContain('45 days');
    expect(ageOptions(0)[0]).toEqual({ id: 0, label: 'Off' });
  });

  it('Preview keeps the server order (delete-first trackers, then the rest)', async () => {
    stub();
    view();
    await waitFor(() => screen.getByTestId('downloads-guard'));
    fireEvent.click(screen.getByTestId('dg-preview'));
    await screen.findByText('Would delete 60 GB of 100 GB');
    expect(screen.getAllByTestId('dg-preview-list-row').map(r => r.textContent?.split('IPTorrents')[0].split('BeyondHD')[0]))
      .toEqual(['Heat.1995', 'Zodiac.2007']);
  });

  it('Never delete: shows tags and hashes (last 4), adds a tag, and asks to sign in to remove one', async () => {
    const calls = stub((u, init) => {
      if (init?.method === 'PUT' && JSON.parse(String(init.body)).protected_tags?.length === 0) return json({ error: 'Sign in', sign_in: true }, 401);
      return undefined;
    });
    view();
    await waitFor(() => screen.getByTestId('downloads-guard'));
    expect(screen.getByText('Never delete')).toBeTruthy();
    expect(screen.getAllByTestId('dg-never-tag').map(t => t.textContent?.replace('×', ''))).toEqual(['luma-keep']);
    expect(screen.getAllByTestId('dg-never-hash').map(t => t.textContent?.replace('×', ''))).toEqual(['…efb6']);
    const add = screen.getByTestId('dg-never-add');
    fireEvent.change(add, { target: { value: 'hold' } });
    fireEvent.keyDown(add, { key: 'Enter' });
    await waitFor(() => expect(puts(calls)).toEqual([{ protected_tags: ['luma-keep', 'hold'] }]));
    fireEvent.change(add, { target: { value: 'LUMA-KEEP' } });
    fireEvent.keyDown(add, { key: 'Enter' });
    fireEvent.click(screen.getByTestId('dg-never-remove-hold'));
    fireEvent.click(screen.getByTestId('dg-never-remove-luma-keep'));
    await waitFor(() => screen.getByTestId('dg-sign-in'));
    expect(puts(calls)).toEqual([{ protected_tags: ['luma-keep', 'hold'] }, { protected_tags: ['luma-keep'] }, { protected_tags: [] }]);
  });

  it('scope picks only this install\'s downloads', async () => {
    const calls = stub();
    view();
    await waitFor(() => screen.getByTestId('downloads-guard'));
    fireEvent.click(screen.getByText('All finished downloads'));
    fireEvent.click(await screen.findByText("Only this install's downloads"));
    await waitFor(() => expect(puts(calls)).toEqual([{ scope: 'test_tagged' }]));
  });

  it('is off on the test instance until ops opts in', async () => {
    stub(undefined, false);
    view();
    await waitFor(() => screen.getByTestId('downloads-guard'));
    expect(screen.getByTestId('dg-state').textContent).toContain('Off on the test instance');
    expect((screen.getByTestId('dg-run') as HTMLButtonElement).disabled).toBe(true);
  });

  it("describes a run like prod's log", () => {
    expect(describeRun({ dry_run: false, triggered: true, freed_bytes: 120 * GIB, deleted: [{ name: 'a', size_bytes: 1 }], failed: [], free_after_bytes: 270 * GIB }))
      .toBe('Freed 120 GB across 1 download · 270 GB free now');
    expect(describeRun({ dry_run: false, triggered: false, reason: 'Above the floor', freed_bytes: 0, deleted: [], failed: [] })).toBe('Above the floor');
    expect(describeRun({ dry_run: false, triggered: true, error: "Can't reach Plex, so nothing was deleted", freed_bytes: 0, deleted: [], failed: [] }))
      .toBe("Can't reach Plex, so nothing was deleted");
  });
});
