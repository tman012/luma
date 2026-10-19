/** v185: Sonarr-style episode naming in Settings > TV > Options (live examples), and the Series list polish
 * (titles wrap to 2 lines, no ellipsis; Quality profile shows the name or 'Default'). */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NamingSettings, type Naming } from '@/components/library/TvNaming';
import { SeriesList, profileName } from '@/components/library/TvBulk';
import type { Show } from '@/components/library/tvLibraryApi';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const N: Naming = {
  rename_episodes: true, standard: '{Series Title} - S{season:00}E{episode:00} - {Episode Title}',
  daily: '{Series Title} - {Air-Date} - {Episode Title} {Quality Full}',
  anime: '{Series Title} - S{season:00}E{episode:00} - {Episode Title} {Quality Full}', series_folder: '{Series Title}',
  season_folder: 'Season {season}', specials_folder: 'Specials', multi_episode_style: 'prefixed_range', colon_replacement: 'dash',
  replace_illegal: true,
};
const calls: { url: string; method: string; body: { naming: Partial<Naming> } }[] = [];
beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url: String(u), method: init?.method || 'GET', body });
    if (String(u).endsWith('/naming/preview')) {
      const std = body.naming.standard as string;
      if (std.includes('{Nope}')) return res({ examples: { standard: '' }, errors: { 'naming.standard': 'Unknown token {Nope}' } });
      return res({ examples: { standard: std.includes('x{episode') ? 'Meltdown- Three Mile Island - 01x01.mkv' : 'Meltdown- Three Mile Island - S01E01 - The Accident.mkv',
        series_folder: 'Meltdown- Three Mile Island', season_folder: 'Season 1', specials_folder: 'Specials',
        multi_episode_style: 'The IT Crowd - S01E01-E03 - A + B + C.mkv' }, errors: {} });
    }
    if (String(u) === '/api/settings/tv' && init?.method === 'PUT') return res({ paths: [], naming: { ...N, ...body.naming } });
    return res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Episode naming', () => {
  it('sentence-case labels, no helper text, a live example under each format field', async () => {
    render(<NamingSettings value={N} onSaved={() => {}} say={() => {}} />);
    expect(screen.getByText('Episode naming')).toBeTruthy();
    for (const l of ['Rename episodes', 'Standard episode format', 'Daily episode format', 'Anime episode format', 'Series folder format',
      'Season folder format', 'Specials folder format', 'Multi-episode style', 'Colon replacement', 'Replace illegal characters']) {
      expect(screen.getByText(l)).toBeTruthy();
    }
    await waitFor(() => expect(screen.getByTestId('tv-naming-standard-example').textContent).toBe('Meltdown- Three Mile Island - S01E01 - The Accident.mkv'));
    expect(screen.getByTestId('tv-naming-series_folder-example').textContent).toBe('Meltdown- Three Mile Island');
    expect(screen.getByTestId('tv-naming-season_folder-example').textContent).toBe('Season 1');
    expect(screen.getByTestId('tv-naming-multi_episode_style-example').textContent).toBe('The IT Crowd - S01E01-E03 - A + B + C.mkv');
    expect(calls.filter(c => c.method === 'PUT')).toHaveLength(0);              // previews save nothing
  });

  it('typing updates the example live; a valid format saves on leaving the field; a bad one shows its error and saves nothing', async () => {
    const saved = vi.fn();
    render(<NamingSettings value={N} onSaved={saved} say={() => {}} />);
    const input = screen.getByTestId('tv-naming-standard');
    fireEvent.change(input, { target: { value: '{Series Title} - {season:00}x{episode:00}' } });
    await waitFor(() => expect(screen.getByTestId('tv-naming-standard-example').textContent).toBe('Meltdown- Three Mile Island - 01x01.mkv'));
    fireEvent.blur(input);
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(calls.find(c => c.method === 'PUT')!.body).toEqual({ naming: { standard: '{Series Title} - {season:00}x{episode:00}' } });
    calls.length = 0;
    fireEvent.change(input, { target: { value: '{Nope}' } });
    await waitFor(() => expect(screen.getByTestId('tv-naming-standard-error').textContent).toBe('Unknown token {Nope}'));
    fireEvent.blur(input);
    await act(async () => { await new Promise(r => setTimeout(r, 50)); });
    expect(calls.filter(c => c.method === 'PUT')).toHaveLength(0);
  });

  it('the switches and menus save at once', async () => {
    render(<NamingSettings value={N} onSaved={() => {}} say={() => {}} />);
    fireEvent.click(screen.getByTestId('tv-naming-rename-toggle'));
    await waitFor(() => expect(calls.find(c => c.method === 'PUT')!.body).toEqual({ naming: { rename_episodes: false } }));
  });
});

const show = (id: number, title: string, profile_id: number | null): Show => ({
  id, tmdb_id: id, title, year: 2020, path_id: 'p_0000aaaa', path_name: 'TV', folder: title, poster_path: null, monitored: true, files: 0,
  episodes: 10, linked: 0, mismatch: 0, unrecognized: 0, aired: 10, have: 3, profile_id, monitor: 'all',
});

describe('Series list polish', () => {
  it('long titles wrap to 2 lines with no ellipsis; Quality profile is the name or Default', () => {
    const long = 'The Extraordinarily Long Title of a Documentary Series About Something Important';
    render(<SeriesList shows={[show(1, long, null), show(2, 'Severance', 7), show(3, 'Gone', 99)]} profiles={[{ id: 7, name: 'HD-1080p' } as never]}
      picked={new Set()} setPicked={() => {}} onOpen={() => {}} />);
    const t = screen.getAllByTestId('tv-list-open')[0]!;
    expect(t.style.whiteSpace).toBe('normal');
    expect(t.style.textOverflow).toBe('');
    expect(t.style.maxHeight).toBe('36px');
    expect(screen.getAllByTestId('tv-list-profile').map(e => e.textContent)).toEqual(['Default', 'HD-1080p', 'Default']);
    expect(profileName(null, new Map())).toBe('Default');
  });
});
