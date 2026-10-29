import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { GenreRouletteSettings, grCss } from '@/components/automation/GenreRouletteSettings';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const ok = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const run = (id: number) => ({
  id, started_at: 1761000000 - id * 86400, finished_at: null, kind: 'scheduled', dry: false, status: 'done', wanted: 1, grabbed: 1,
  stop_reason: null, genres: ['Thriller'], error: null, picks: [{ tmdb_id: id, title: `Movie ${id}`, year: 2010, genre: 'Thriller', poster_path: null }], failures: [],
});

describe('automation History scrolls inside the Status column', () => {
  it('css: grid stretches, History flexes and scrolls with a thin scrollbar; stacked layout caps height', () => {
    expect(grCss).toMatch(/\.gr-grid\.gr-hgrid\{align-items:stretch\}/);
    expect(grCss).toMatch(/\.gr-hpanel\{display:flex;flex-direction:column;min-height:0\}/);
    expect(grCss).toMatch(/\.gr-hscroll\{position:absolute;inset:0;overflow-y:auto/);
    expect(grCss).toMatch(/scrollbar-width:thin/);
    expect(grCss).toMatch(/@media \(max-width:899px\)\{[^}]*\}[^@]*\.gr-hscroll\{position:static;max-height:360px\}/);
  });
  it('GR History list sits in the scroll box, all rows still rendered', async () => {
    const history = Array.from({ length: 30 }, (_, i) => run(i + 1));
    vi.stubGlobal('fetch', vi.fn(async () => ok({
      settings: { enabled: true, dry_run: false, time: '22:50', days: 'daily', genres_per_run: 1, movies_per_genre: 1, include_genres: [], exclude_genres: [],
        year_from: null, year_to: null, language: 'any', min_votes: 0, imdb_min_rating: 0, imdb_min_votes: 0, profile_id: null, min_seeders: 0, skip_same_collection: true },
      genres: ['Thriller'], imdb_active: false, profiles: [],
      status: { running: false, enabled: true, next_run: null, failures_7d: 0, last_run: history[0] }, history,
    })));
    render(<GenreRouletteSettings />);
    const list = await screen.findByTestId('gr-history');
    expect(list.className).toBe('gr-hscroll');
    expect(list.parentElement?.className).toBe('gr-hbox');
    expect(screen.getByTestId('gr-status').className).toBe('gr-hpanel');
    expect(screen.getAllByTestId('gr-history-row')).toHaveLength(30);
  });
});
