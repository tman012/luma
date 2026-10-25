import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TrendingTvSettings, counts, normTtv, skips } from '@/components/automation/TrendingTvSettings';

const SETTINGS = {
  enabled: true, limit: 10, profile_id: null, search_on_add: true, languages: ['en'],
  allow_unknown_language: true, skip_plex: true, skip_if_plex_unreadable: true, no_readd: true,
  page: 1, path_id: null, monitor: 'all' as const, require_tvdb: true, one_season_only: true,
  interval_hours: 2, when_busy: 'skip' as const, history_kept: 200 as const,
};
const LAST = {
  checked: 10, added: 2, skipped: { existing: 3, plex: 2, language: 1, seasons: 2, no_tvdb: 0, no_tmdb: 0, removed: 0 },
  errors: [], error: null,
};
const HIST = [
  { id: 2, tmdb_id: 9, tvdb_id: 19, title: 'Fallout', year: 2024, added_at: Date.parse('2026-10-25T12:00:00-04:00') / 1000 },
  { id: 1, tmdb_id: 5, tvdb_id: 7, title: 'Shogun', year: 2024, added_at: Date.parse('2026-10-25T03:05:00-04:00') / 1000 },
];
const view = (over: Record<string, unknown> = {}) => ({
  settings: SETTINGS, monitor: ['all'], languages: ['en', 'fr', 'ja'],
  profiles: [{ id: 1, name: 'Any', default: false }, { id: 2, name: 'HD-1080p', default: true }],
  libraries: [{ id: 'p_tv', name: 'TV' }, { id: 'p_kids', name: 'Kids TV' }],
  status: { running: false, enabled: true, next_run: Date.parse('2026-10-25T14:00:00-04:00') / 1000,
    last_run: Date.parse('2026-10-25T12:00:00-04:00') / 1000, last_kind: 'manual', last_result: LAST },
  history: HIST, ...over,
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const ok = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });

function stub(handler?: (url: string, init?: RequestInit) => Response | undefined) {
  const calls: { url: string; init?: RequestInit }[] = [];
  let cur = view();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const h = handler?.(url, init);
    if (h) return h;
    if (init?.method === 'PUT') {
      const p = JSON.parse(String(init.body));
      cur = view({ settings: { ...cur.settings, ...p } });
      return ok(cur);
    }
    if (init?.method === 'POST') return ok({ ok: true, started: true }, 202);
    if (url.endsWith('/status')) return ok({ status: cur.status, history: cur.history });
    return ok(cur);
  }));
  return calls;
}

describe('v279 Settings > Automation > Trending TV', () => {
  it('shows prod defaults, one-season toggle, status and history', async () => {
    stub();
    render(<TrendingTvSettings />);
    await waitFor(() => expect(screen.getByTestId('trending-tv')).toBeTruthy());
    expect(screen.getByRole('heading', { name: 'Trending TV' })).toBeTruthy();
    for (const t of ['Check every', 'When Trakt is busy', 'History kept', 'Shows per run', 'Library', 'Monitor',
      'One season only', 'Search when added', 'Skip shows in Plex', "Don't re-add removed shows"]) {
      expect(screen.getByText(t)).toBeTruthy();
    }
    expect(screen.getByTestId('ttv-every-select').textContent).toBe('2 hours');
    expect(screen.getByTestId('ttv-limit-value').textContent).toBe('10');
    expect(screen.getByRole('switch', { name: 'Trending TV' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('ttv-one-season-toggle').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('Fallout')).toBeTruthy();
    expect(screen.getByText('Added 2 of 10')).toBeTruthy();
  });

  it('saves enabled and one_season_only patches', async () => {
    const calls = stub();
    render(<TrendingTvSettings />);
    await waitFor(() => expect(screen.getByTestId('trending-tv')).toBeTruthy());
    fireEvent.click(screen.getByRole('switch', { name: 'Trending TV' }));
    await waitFor(() => expect(calls.some(c => c.init?.method === 'PUT')).toBe(true));
    const body = JSON.parse(String(calls.find(c => c.init?.method === 'PUT')!.init!.body));
    expect(body.enabled).toBe(false);
  });

  it('helpers and normTtv', () => {
    expect(counts(LAST)).toBe('Added 2 of 10');
    expect(skips(LAST)).toContain('not 1 season 2');
    expect(normTtv(null)).toBeNull();
    expect(normTtv(view())?.settings.limit).toBe(10);
  });
});
