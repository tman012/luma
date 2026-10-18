/**
 * v175 (PM): Movie night, Genre roulette and Franchise completer "Run now" (a real run, dry_run false)
 * opens "Start a real run?" with Run (primary #3b82f6) and Cancel, no helper text, in Designer's glass
 * dialog. Cancel (also Escape and the scrim) sends nothing; Run sends exactly the same single POST as
 * before. Previews (dry runs) stay one click.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider } from '@/contexts/AuthContext';
import { SettingsProvider } from '@/pages/settings/SettingsContext';
import { MovieNightCard } from '@/pages/settings/MovieNightCard';
import { GenreRouletteCard } from '@/pages/settings/GenreRouletteCard';
import { FranchiseCompleterCard } from '@/pages/settings/FranchiseCompleterCard';
import { CANCEL_BTN, REAL_RUN_TITLE, RUN_BTN } from '@/pages/settings/components/RealRunConfirm';

const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let calls: { url: string; method: string; body: unknown }[] = [];

function stubApi() {
  calls = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = (init?.method || 'GET').toUpperCase();
    calls.push({ url: u, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (u.includes('/api/auth/me')) return res({ authenticated: true, user: { id: 1, name: 'o', is_admin: true } });
    if (u.endsWith('/api/settings')) return res({ automation: { movie_night: { enabled: true }, genre_roulette: { enabled: true },
      franchise_completer: { enabled: true } } });
    if (u.includes('/api/settings/schema')) return res({});
    if (u.includes('quality-profiles')) return res([]);
    if (u.includes('/trackers') || u.includes('/genres')) return res({ selectable: false, trackers: [], genres: [] });
    if (u.includes('canon')) return res({ total: 0, owned: 0, missing: 0 });
    if (u.includes('/status')) return res({ status: { running: false, last_run: null }, log: [], running: false });
    if (u.includes('/preview')) return res({ ok: true, dry_run: true, picks: [], candidates: 0, collections_eligible: 0 });
    if (u.includes('/run')) return res({ ok: true, dry_run: false, triggered: true });
    return res({});
  }));
}

const wrap = (el: React.ReactNode) => render(<MemoryRouter><AuthProvider><SettingsProvider>{el}</SettingsProvider></AuthProvider></MemoryRouter>);
const writes = () => calls.filter(c => c.method !== 'GET');
const runs = (path: string) => calls.filter(c => c.url.includes(path) && c.method === 'POST');

const CARDS = [
  ['Movie night', () => <MovieNightCard />, '/api/movie-night/run', 'Preview pick', '/api/movie-night/preview'],
  ['Genre roulette', () => <GenreRouletteCard />, '/api/genre-roulette/run', 'Preview spin', '/api/genre-roulette/preview'],
  ['Franchise completer', () => <FranchiseCompleterCard />, '/api/franchise-completer/run', 'Preview gaps', '/api/franchise-completer/preview'],
] as const;

describe('v175: a real run asks "Start a real run?" first', () => {
  beforeEach(() => { localStorage.clear(); stubApi(); vi.stubGlobal('confirm', vi.fn(() => true)); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it.each(CARDS)('%s: Run now opens the dialog and sends nothing yet', async (_n, el, path) => {
    wrap(el());
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }));
    const dlg = await screen.findByRole('alertdialog', { name: REAL_RUN_TITLE });
    expect(dlg.textContent).toBe(`${REAL_RUN_TITLE}CancelRun`);             // title and two buttons, no helper text
    const run = screen.getByRole('button', { name: 'Run' });
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    expect(run.className).toBe(RUN_BTN);
    expect(RUN_BTN).toContain('bg-[#3b82f6]');
    expect(cancel.className).toBe(CANCEL_BTN);
    expect(document.activeElement).toBe(cancel);                            // the safe choice has focus
    expect(runs(path)).toHaveLength(0);
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it.each(CARDS)('%s: Cancel, Escape and the scrim send nothing', async (_n, el, path) => {
    wrap(el());
    const before = writes().length;
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
    await screen.findByRole('alertdialog');
    act(() => { fireEvent.keyDown(window, { key: 'Escape' }); });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
    fireEvent.click(await screen.findByTestId('real-run-scrim'));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    await new Promise(r => setTimeout(r, 50));
    expect(runs(path)).toHaveLength(0);
    expect(writes().length).toBe(before);
  });

  it.each(CARDS)('%s: Run sends exactly the same single POST as before', async (_n, el, path) => {
    wrap(el());
    const before = writes().length;
    fireEvent.click(await screen.findByRole('button', { name: 'Run now' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Run' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    await waitFor(() => expect(runs(path)).toHaveLength(1));
    await new Promise(r => setTimeout(r, 50));
    expect(runs(path)).toHaveLength(1);
    expect(runs(path)[0].body).toEqual({ dry_run: false });
    expect(writes().length).toBe(before + 1);                                // nothing else written
  });

  it.each(CARDS)('%s: the preview (dry run) stays one click, no dialog', async (_n, el, path, preview, previewPath) => {
    wrap(el());
    fireEvent.click(await screen.findByRole('button', { name: preview }));
    await waitFor(() => expect(calls.some(c => c.url.includes(previewPath))).toBe(true));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(runs(path)).toHaveLength(0);
  });
});
