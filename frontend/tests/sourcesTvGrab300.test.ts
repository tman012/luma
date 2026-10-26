/** v300: Sources TV grabs post to /api/settings/tv/grab (tv_grab); movies stay on /api/downloads/add. */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { grabToLibrary } from '@/services/allSourcesApi';

const res = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

afterEach(() => vi.unstubAllGlobals());

describe('grabToLibrary TV → tv_grab', () => {
  it('posts TV to /api/settings/tv/grab with season (never /api/downloads/add)', async () => {
    const seen: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      seen.push({ url: String(url), init });
      return res({ ok: true, series_id: 3 });
    }));
    const r = await grabToLibrary({
      downloadUrl: '/api/trackers/release/abc',
      torrentName: 'Bluey.S01.1080p',
      tmdbId: 82728,
      mediaType: 'tv',
      title: 'Bluey',
      year: 2018,
      season: 1,
      episode: null,
    });
    expect(r.success).toBe(true);
    expect(seen).toHaveLength(1);
    expect(seen[0]!.url).toBe('/api/settings/tv/grab');
    expect(seen[0]!.init!.method).toBe('POST');
    expect(JSON.parse(String(seen[0]!.init!.body))).toEqual({
      tmdb_id: 82728,
      season: 1,
      episode: null,
      download_url: '/api/trackers/release/abc',
      title: 'Bluey.S01.1080p',
    });
  });

  it('refuses TV without a season', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const r = await grabToLibrary({
      downloadUrl: 'x',
      torrentName: 't',
      tmdbId: 1,
      mediaType: 'tv',
      title: 'Show',
    });
    expect(r.success).toBe(false);
    expect(r.error).toMatch(/season/i);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('keeps movies on /api/downloads/add with movie tag path', async () => {
    const seen: { url: string; body: unknown }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      seen.push({ url: String(url), body: JSON.parse(String(init?.body)) });
      return res({ success: true, message: 'Download added successfully' });
    }));
    const r = await grabToLibrary({
      downloadUrl: 'magnet:?xt=urn:btih:' + 'a'.repeat(40),
      torrentName: 'Heat.1995',
      tmdbId: 949,
      mediaType: 'movie',
      title: 'Heat',
      year: 1995,
    });
    expect(r.success).toBe(true);
    expect(seen[0]!.url).toBe('/api/downloads/add');
    expect((seen[0]!.body as { tmdb_data: { media_type: string } }).tmdb_data.media_type).toBe('movie');
  });
});

describe('TvSourcesPanel wires season into grabToLibrary', () => {
  const src = readFileSync(
    resolve(__dirname, '../src/components/media/TvDetailModal/TvSourcesPanel.tsx'),
    'utf8',
  );
  it('passes target.season / episode into grabToLibrary (both panels)', () => {
    const hits = src.split('season: target.season,').length - 1;
    expect(hits).toBeGreaterThanOrEqual(2); // compact + expanded runLibraryGrab (+ popupGrab)
    expect(src).toContain("mediaType: 'tv'");
    expect(src).toContain('episode: target.type === \'episode\'');
  });
  it('does not call /api/downloads/add from the panel source', () => {
    expect(src).not.toContain("/api/downloads/add");
  });
});
