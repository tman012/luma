/** v158a (docs/design/tv-edit.md): calendar bookmark + dimmed unmonitored cards, Series Select + bulk
 * Missing, the Edit series modal, TV quality profiles, the detail toggles, TV detail polish. */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WeekGrid, DIM } from '@/components/library/TvCalendar';
import { TvSeries } from '@/components/library/TvSeries';
import { EditSeriesModal, QualityProfiles } from '@/components/library/TvEdit';
import { EpisodeMark, LumaMonitorContext, SeasonMark, SeriesMark, useLumaSeries } from '@/components/library/tvMonitor';
import { cutoffLabel, type CalEpisode, type SeriesView } from '@/components/library/tvCalendarApi';
import type { Show } from '@/components/library/tvLibraryApi';
import { seasonsLabel } from '@/utils/seasonsLabel';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
const calls: { url: string; method: string; body: unknown }[] = [];
let handler: (url: string, method: string, body: unknown) => Response | null;
beforeEach(() => {
  calls.length = 0;
  handler = () => null;
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    const url = String(u); const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    return handler(url, method, body) ?? res({ error: 'Not found' }, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__; });

const VIEW: SeriesView = {
  id: 7, title: 'The Reluctant Vampire', year: 2025, tmdb_id: 700, monitored: true, monitor: 'future', monitor_new_seasons: 'all',
  monitor_specials: false, season_folder: true, series_type: 'standard', profile_id: 1, path_id: 'p_0000aaaa', folder: 'The Reluctant Vampire (2025)',
  path: '/media/TV/The Reluctant Vampire (2025)', seasons: [{ season: 1, monitored: true }],
  episodes: [{ id: 71, season: 1, episode: 1, monitored: false }, { id: 72, season: 1, episode: 2, monitored: true }],
};
const PROFILES = [
  { id: 1, name: 'HD', items: [{ id: '1080p-web', allowed: true }, { id: '720p-web', allowed: true }], cutoff: '1080p-web', upgrades: true, default: true, in_use: true },
  { id: 2, name: 'Any', items: [{ id: '720p-hdtv', allowed: true }], cutoff: '720p-hdtv', upgrades: false, default: false, in_use: false },
];
const QUALITIES = ['1080p-web', '720p-web', '720p-hdtv'].map(id => ({ id, name: id.replace('-web', ' WEB-DL').replace('-hdtv', ' HDTV'), resolution: id.split('-')[0]!, source: id.split('-')[1]! }));

// ---------------------------------------------------------------- calendar

describe('calendar bookmark', () => {
  const ep = (id: number, monitored: boolean): CalEpisode => ({ episode_id: id, series_id: 1, tmdb_id: 95396, show: 'Severance', year: 2022,
    poster_path: null, season: 2, episode: id, title: `Episode ${id}`, air_date: '2026-10-12', downloaded: id === 1, monitored });
  it('filled = monitored; unmonitored episodes are not in the week grid (v184); a click unmonitors that episode only and hides it', async () => {
    handler = (url, method) => (method === 'PUT' && url.startsWith('/api/settings/tv/episodes/') ? res({ ...VIEW, id: 1 }) : null);
    render(<WeekGrid eps={[ep(1, true), ep(2, false)]} week="2026-10-11" today="2026-10-18" />);
    const cards = screen.getAllByTestId('tv-agenda-row');
    expect(cards.map(c => c.getAttribute('data-monitored'))).toEqual(['true']);
    expect(within(cards[0]!).getByTestId('tv-card-body').style.opacity).toBe('1');
    expect(DIM).toBe(0.5);
    const mark = within(cards[0]!).getByTestId('tv-card-bookmark');
    expect([mark.getAttribute('aria-pressed'), mark.getAttribute('aria-label')]).toEqual(['true', 'Monitored: Severance S02E01']);
    expect(mark.querySelector('svg')!.getAttribute('fill')).toBe('currentColor');
    expect(mark.textContent).toBe('');                                                          // no label on screen
    fireEvent.click(mark);
    expect(screen.queryAllByTestId('tv-agenda-row')).toHaveLength(0);
    await waitFor(() => expect(calls).toEqual([{ url: '/api/settings/tv/episodes/1/monitored', method: 'PUT', body: { monitored: false } }]));
  });

  it('a failed toggle snaps back', async () => {
    handler = () => res({ error: 'nope' }, 500);
    render(<WeekGrid eps={[ep(1, true)]} week="2026-10-11" today="2026-10-18" />);
    fireEvent.click(screen.getByTestId('tv-card-bookmark'));
    await waitFor(() => expect(screen.getByTestId('tv-card-bookmark').getAttribute('aria-pressed')).toBe('true'));
  });

  it('the bookmark shows on hover / focus (always on touch)', () => {
    const css = readFileSync(resolve(__dirname, '../src/components/library/TvCalendar.tsx'), 'utf8');
    expect(css).toContain('.tvc-card:hover .tvc-bm,.tvc-card:focus-within .tvc-bm');
    expect(css).toContain('@media (hover:none){.tvc-card .tvc-bm{opacity:1}}');
  });
});

// ---------------------------------------------------------------- series grid

const show = (id: number, title: string): Show => ({ id, tmdb_id: id, title, year: 2020, path_id: 'p', path_name: 'TV', folder: title,
  poster_path: null, monitored: true, files: 0, episodes: 0, linked: 0, mismatch: 0, unrecognized: 0, aired: 10, have: 5, continuing: false });

describe('Series: Select and bulk Missing; a poster opens Edit series', () => {
  it('Select, pick two, Monitor missing posts their ids; Cancel leaves select mode', async () => {
    handler = (url, method) => (url === '/api/settings/tv/shows/monitor-missing' && method === 'POST' ? res({ ok: true, updated: 2 }) : null);
    const changed = vi.fn();
    render(<TvSeries shows={[show(1, 'Andor'), show(2, 'Bluey'), show(3, 'Cops')]} onChanged={changed} />);
    fireEvent.click(screen.getByTestId('tv-series-select'));
    const go = screen.getByTestId('tv-series-missing') as HTMLButtonElement;
    expect([go.textContent, go.disabled, go.style.opacity]).toEqual(['Monitor missing', true, '0.4']);
    const opens = screen.getAllByTestId('tv-series-open');
    fireEvent.click(opens[0]!); fireEvent.click(opens[2]!);
    expect(opens.map(o => o.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'true']);
    expect(screen.getByTestId('tv-series-picked').textContent).toBe('2 selected');
    expect(screen.queryByTestId('tv-edit-series')).toBeNull();                    // selecting never opens the sheet
    fireEvent.click(go);
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(calls.find(c => c.method === 'POST')!.body).toEqual({ ids: [1, 3] });
    expect(screen.getByTestId('tv-series-select')).toBeTruthy();
  });

  it('a poster click opens Edit <title>', async () => {
    handler = url => (url === '/api/settings/tv/shows/2' ? res({ ...VIEW, id: 2 }) : url === '/api/settings/tv/profiles' ? res({ profiles: PROFILES, qualities: QUALITIES }) : null);
    render(<TvSeries shows={[show(1, 'Andor'), show(2, 'Bluey')]} />);
    fireEvent.click(screen.getAllByTestId('tv-series-open')[1]!);
    const modal = await screen.findByTestId('tv-edit-series');
    expect(modal.getAttribute('aria-label')).toBe('Edit Bluey');
  });
});

// ---------------------------------------------------------------- edit series

describe('Edit series modal', () => {
  beforeEach(() => {
    handler = (url, method, body) => {
      if (url === '/api/settings/tv/shows/7' && method === 'GET') return res(VIEW);
      if (url === '/api/settings/tv/shows/7' && method === 'PUT') return res({ ...VIEW, ...(body as object) });
      if (url.startsWith('/api/settings/tv/shows/7') && method === 'DELETE') return res({ ok: true, files_removed: url.includes('delete_files') });
      if (url === '/api/settings/tv/profiles') return res({ profiles: PROFILES, qualities: QUALITIES });
      return null;
    };
  });

  it('rows in the spec order; Save disabled until a change, then saves only what changed', async () => {
    const saved = vi.fn(); const close = vi.fn();
    render(<EditSeriesModal seriesId={7} title="The Reluctant Vampire" onClose={close} onSaved={saved} />);
    const m = await screen.findByTestId('tv-edit-series');
    await within(m).findByTestId('tve-monitored-toggle');
    expect(within(m).getByRole('heading').textContent).toBe('Edit The Reluctant Vampire');
    const labels = [...m.querySelectorAll('label')].map(l => l.textContent);
    expect(labels).toEqual(['Monitored', 'Monitor new seasons', 'Specials', 'Season folders', 'Quality profile', 'Series type', 'Path']);
    expect(m.textContent).not.toMatch(/Tags|e\.g\.|example/i);                     // no tags, no Sonarr grey text
    const save = within(m).getByTestId('tve-save') as HTMLButtonElement;
    expect([save.disabled, save.style.opacity]).toEqual([true, '0.4']);
    const sw = within(m).getByTestId('tve-monitored-toggle');
    expect([sw.getAttribute('role'), sw.getAttribute('aria-checked'), sw.style.width, sw.style.height]).toEqual(['switch', 'true', '40px', '22px']);
    fireEvent.click(sw);
    fireEvent.click(within(m).getByTestId('tve-type-select'));
    expect(within(m).getAllByRole('option').map(o => o.textContent)).toEqual(['Standard', 'Daily', 'Anime']);
    fireEvent.click(within(m).getByRole('option', { name: 'Anime' }));
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(calls.find(c => c.method === 'PUT')!.body).toEqual({ monitored: false, series_type: 'anime' });
    expect(close).toHaveBeenCalled();
    const frame = m.parentElement!;
    expect(frame.style.background).toBe('rgba(10, 8, 24, 0.6)');                  // the scrim
    expect([m.style.width, m.style.borderRadius, m.style.padding]).toEqual(['560px', '16px', '24px']);
  });

  it("a failed save says Couldn't save in the footer", async () => {
    const base = handler;
    handler = (url, method, body) => (method === 'PUT' ? res({ error: 'x' }, 500) : base(url, method, body));
    render(<EditSeriesModal seriesId={7} title="The Reluctant Vampire" onClose={() => {}} />);
    const m = await screen.findByTestId('tv-edit-series');
    fireEvent.click(await within(m).findByTestId('tve-season-folder-toggle'));
    fireEvent.click(within(m).getByTestId('tve-save'));
    expect((await within(m).findByTestId('tve-error')).textContent).toBe("Couldn't save");
  });

  it('Delete swaps the footer in place: Delete <title>?, Delete files too, Cancel, Delete', async () => {
    const deleted = vi.fn();
    render(<EditSeriesModal seriesId={7} title="The Reluctant Vampire" onClose={() => {}} onDeleted={deleted} />);
    const m = await screen.findByTestId('tv-edit-series');
    await within(m).findByTestId('tve-monitored-toggle');
    const del = within(m).getByTestId('tve-delete');
    expect([del.style.color, del.style.border]).toEqual(['rgb(239, 68, 68)', '1px solid rgba(239, 68, 68, 0.3)']);
    fireEvent.click(del);
    expect(screen.getAllByRole('dialog')).toHaveLength(1);                         // no second modal
    expect(within(m).getByTestId('tve-confirm').textContent).toBe('Delete The Reluctant Vampire?');
    fireEvent.click(within(m).getByTestId('tve-files-toggle'));
    fireEvent.click(within(m).getByTestId('tve-delete-confirm'));
    await waitFor(() => expect(deleted).toHaveBeenCalled());
    expect(calls.find(c => c.method === 'DELETE')!.url).toBe('/api/settings/tv/shows/7?delete_files=1');
  });
});

// ---------------------------------------------------------------- quality profiles

describe('TV quality profiles', () => {
  it('rows: name, Default chip, Up to <res>; empty card; Add profile', async () => {
    handler = url => (url === '/api/settings/tv/profiles' ? res({ profiles: PROFILES, qualities: QUALITIES }) : null);
    render(<QualityProfiles />);
    const rows = await screen.findAllByTestId('tv-profile-row');
    expect(rows.map(r => r.textContent)).toEqual(['HDDefaultUp to 1080p', 'AnyUp to 720p']);
    expect(rows[0]!.style.height).toBe('56px');
    expect(cutoffLabel('sd-hdtv')).toBe('Up to SD');
    cleanup();
    handler = url => (url === '/api/settings/tv/profiles' ? res({ profiles: [], qualities: QUALITIES }) : null);
    render(<QualityProfiles />);
    expect((await screen.findByTestId('tv-profiles-empty')).textContent).toBe('No quality profiles');
  });

  it('New profile: Upgrades off hides Upgrade until; a name and one quality enable Save; posts the ordered list', async () => {
    handler = (url, method) => (url === '/api/settings/tv/profiles' && method === 'GET' ? res({ profiles: [], qualities: QUALITIES })
      : url === '/api/settings/tv/profiles' && method === 'POST' ? res({ ...PROFILES[1], id: 9 }, 201) : null);
    render(<QualityProfiles />);
    fireEvent.click(await screen.findByTestId('tv-profile-add'));
    const m = screen.getByTestId('tv-profile-editor');
    expect(within(m).getByRole('heading').textContent).toBe('New profile');
    expect(within(m).getByTestId('tvp-cutoff-row').style.height).toBe('0px');
    expect(within(m).queryByTestId('tvp-delete')).toBeNull();
    const save = within(m).getByTestId('tvp-save') as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(within(m).getByTestId('tvp-name'), { target: { value: 'Web' } });
    expect(save.disabled).toBe(true);                                            // no quality yet
    const checks = within(m).getAllByTestId('tvp-quality-check');
    fireEvent.click(checks[1]!);
    fireEvent.click(within(m).getByTestId('tvp-upgrades-toggle'));
    expect(within(m).getByTestId('tvp-cutoff-row').style.opacity).toBe('1');
    const rows = within(m).getAllByTestId('tvp-quality');
    fireEvent.keyDown(rows[1]!, { key: 'ArrowUp', altKey: true });                // reorder: top = most preferred
    fireEvent.click(save);
    await waitFor(() => expect(calls.some(c => c.method === 'POST')).toBe(true));
    expect(calls.find(c => c.method === 'POST')!.body).toEqual({ name: 'Web', upgrades: true, default: false, cutoff: '720p-web',
      items: [{ id: '720p-web', allowed: true }, { id: '1080p-web', allowed: false }, { id: '720p-hdtv', allowed: false }] });
  });

  it('Delete is hidden on the default profile and on one a show uses', async () => {
    handler = url => (url === '/api/settings/tv/profiles' ? res({ profiles: PROFILES, qualities: QUALITIES }) : null);
    render(<QualityProfiles />);
    const rows = await screen.findAllByTestId('tv-profile-row');
    fireEvent.click(rows[0]!);
    expect(within(screen.getByTestId('tv-profile-editor')).queryByTestId('tvp-delete')).toBeNull();
    fireEvent.click(screen.getByTestId('tvp-cancel'));
    fireEvent.click(rows[1]!);
    expect(within(screen.getByTestId('tv-profile-editor')).getByTestId('tvp-delete')).toBeTruthy();
  });
});

// ---------------------------------------------------------------- detail toggles

function Detail({ tmdbId }: { tmdbId: number }) {
  const m = useLumaSeries(tmdbId);
  return (
    <LumaMonitorContext.Provider value={m}>
      <SeriesMark /><SeasonMark season={1} />
      <div style={{ position: 'relative' }}><EpisodeMark season={1} episode={1} /></div>
    </LumaMonitorContext.Provider>
  );
}

describe('detail toggles', () => {
  it('nothing outside the Luma TV build or for a show not in the library', async () => {
    handler = url => (url === '/api/settings/tv/shows/by-tmdb/700' ? res(VIEW) : null);
    render(<Detail tmdbId={700} />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByTestId('tv-mon-series')).toBeNull();
    expect(calls).toEqual([]);
  });

  it('series / season / episode bookmarks reflect tv.db and PUT their own route', async () => {
    (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    handler = (url, method) => {
      if (url === '/api/settings/tv/shows/by-tmdb/700') return res(VIEW);
      if (method === 'PUT') return res(VIEW);
      return null;
    };
    render(<Detail tmdbId={700} />);
    const series = await screen.findByTestId('tv-mon-series');
    expect([series.getAttribute('aria-pressed'), series.getAttribute('aria-label')]).toEqual(['true', 'Monitored: The Reluctant Vampire']);
    const ep = screen.getByTestId('tv-mon-episode');
    expect([ep.getAttribute('aria-pressed'), ep.getAttribute('aria-label')]).toEqual(['false', 'Monitored: S01E01']);
    fireEvent.click(ep);
    expect(ep.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByTestId('tv-mon-season'));
    fireEvent.click(series);
    await waitFor(() => expect(calls.filter(c => c.method === 'PUT').map(c => [c.url, c.body])).toEqual([
      ['/api/settings/tv/episodes/71/monitored', { monitored: true }],
      ['/api/settings/tv/shows/7/seasons/1/monitored', { monitored: false }],
      ['/api/settings/tv/shows/7/monitored', { monitored: false }],
    ]));
    expect([series, ep].every(b => !b.textContent)).toBe(true);
  });
});

// ---------------------------------------------------------------- detail polish

describe('TV detail polish', () => {
  const read = (p: string) => readFileSync(resolve(__dirname, '../src/components/media/TvDetailModal', p), 'utf8');
  it("'1 season' / '2 seasons' in sentence case", () => {
    expect([seasonsLabel(1), seasonsLabel(2), seasonsLabel(0)]).toEqual(['1 season', '2 seasons', '0 seasons']);
    // Wide popup keeps sentence case; the phone popup matches prod 1b4ea74's 'N Season(s)' (PM/Designer GO on d4d1fee).
    expect(read('ExpandedLayout.tsx')).toContain('seasonsLabel(show.numberOfSeasons)');
    expect(read('CompactLayout.tsx')).toMatch(/Season\$\{show\.numberOfSeasons/);
  });
  it("'Add to watchlist' is a #3b82f6 primary button, not the white pill", () => {
    const src = read('CompactLayout.tsx');
    expect(src).not.toContain('Add to Watchlist');
    expect(src).toContain("'Add to watchlist'");
    const btn = src.slice(src.indexOf('data-testid="tv-add-watchlist"'), src.indexOf("'Add to watchlist'"));
    expect(btn).toContain("background: '#3b82f6'");
    expect(btn).toContain("color: '#ffffff'");
    expect(btn).not.toContain("borderRadius: '50px'");
    expect(read('AddToSonarrModal.tsx')).not.toContain('Add to Watchlist');
  });
  it('minimal builds keep the monitor toggle, Seasons and Cast (data-modal-keep, v217 as prod); Sonarr-only actions stay hidden', () => {
    const css = readFileSync(resolve(__dirname, '../src/index.css'), 'utf8');
    expect(css).toContain('html.luma-minimal [data-modal-actions]:has(> [data-modal-sources], > [data-modal-keep])');
    expect(css).toContain('> :not([data-modal-sources]):not([data-modal-keep]) {\n  display: none !important;');
    const src = read('ExpandedLayout.tsx');
    expect(src).toContain(`{lumaMon && <span data-modal-keep="" style={{ display: 'contents' }}><SeriesMark /></span>}`);
    expect(src).not.toContain(`{...(lumaMon ? { 'data-modal-keep': '' } : {})}`);
    expect(src).toMatch(/<span data-modal-keep="" style=\{\{ display: 'contents' \}\}>\s*<PillButton\s*focusKey="tm-seasons"/);
    expect(src).toMatch(/<span data-modal-keep="" style=\{\{ display: 'contents' \}\}>\s*<PillButton\s*focusKey="tm-cast"/);
  });
});
