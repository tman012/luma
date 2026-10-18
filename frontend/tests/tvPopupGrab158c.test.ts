/** v158c (PM): the TV Sources popup grabs through Luma's own path; Sonarr is only a fallback. */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { popupGrab, tvJobStatus } from '@/components/library/tvCalendarApi';

const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => vi.unstubAllGlobals());

describe('popupGrab', () => {
  it('posts the release to /api/settings/tv/grab and reads ok', async () => {
    const seen: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { seen.push({ url: String(url), init }); return res({ ok: true, series_id: 3 }); }));
    const r = await popupGrab({ tmdb_id: 95396, season: 2, episode: 3, download_url: '/api/trackers/release/abc', title: 'Severance.S02E03' });
    expect(r).toEqual({ ok: true });
    expect(seen[0]!.url).toBe('/api/settings/tv/grab');
    expect(seen[0]!.init!.method).toBe('POST');
    expect(JSON.parse(String(seen[0]!.init!.body))).toEqual({ tmdb_id: 95396, season: 2, episode: 3, download_url: '/api/trackers/release/abc', title: 'Severance.S02E03' });
  });

  it('passes the fallback and blocked flags through; a 5xx or no answer may fall back', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res({ ok: false, error: 'Grabs are off on this instance', blocked: true, fallback: false })));
    expect(await popupGrab({ tmdb_id: 1, season: 1, download_url: 'x', title: 't' }))
      .toEqual({ ok: false, error: 'Grabs are off on this instance', fallback: false, blocked: true });
    vi.stubGlobal('fetch', vi.fn(async () => res({ ok: false, error: "Couldn't reach qBittorrent", fallback: true })));
    expect(await popupGrab({ tmdb_id: 1, season: 1, download_url: 'x', title: 't' }))
      .toEqual({ ok: false, error: "Couldn't reach qBittorrent", fallback: true, blocked: false });
    vi.stubGlobal('fetch', vi.fn(async () => res({ error: 'boom' }, 500)));
    expect((await popupGrab({ tmdb_id: 1, season: 1, download_url: 'x', title: 't' })).ok).toBe(false);
    vi.stubGlobal('fetch', vi.fn(async () => res({ error: 'Invalid' }, 400)));
    expect(await popupGrab({ tmdb_id: 1, season: 1, download_url: 'x', title: 't' }))
      .toEqual({ ok: false, error: 'Invalid', fallback: false, blocked: false });
  });
});

describe('TV Sources panel', () => {
  const src = readFileSync(resolve(__dirname, '../src/components/media/TvDetailModal/TvSourcesPanel.tsx'), 'utf8');
  it("tries Luma's own path first and falls back to Sonarr only for Sonarr's own rows, never when blocked", () => {
    const luma = src.indexOf('await popupGrab(');
    const sonarr = src.indexOf('await handleGrab(');
    expect(luma).toBeGreaterThan(0);
    expect(sonarr).toBeGreaterThan(luma);
    expect(src).toContain("if (r.blocked || !r.fallback || !sonarrRow)");
    expect(src).toContain("confirmTarget.origin !== 'indexer'");
  });
});

describe('tvJobStatus', () => {
  it('no space reads Not enough space; other errors are capitalised as they are', () => {
    const j = { hash: 'a', name: 'x', state: 'failed' as const, at: 1, error: 'insufficient space', retry: true };
    expect(tvJobStatus(j)).toBe('Not enough space');
    expect(tvJobStatus({ ...j, retry: false, error: 'files not found' })).toBe('Files not found');
    expect(tvJobStatus({ ...j, retry: false, error: null })).toBe('Failed');
  });
});
