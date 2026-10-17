import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const auth = { authenticated: true, loading: false, implicit: false, logout: vi.fn(),
  user: { id: 1, name: 'taylor', is_admin: true } };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));

import { LibraryPathsSettings } from '@/components/library/LibraryPathsSettings';
import { ruleSummary, type LibraryView, type Rule } from '@/components/library/libraryPathsApi';
import { SettingsPage } from '@/pages/LibrariesPage';

const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
type Call = { url: string; method: string; body: Record<string, unknown> | null };
let calls: Call[] = [];
let state: LibraryView;
let answer: ((c: Call) => Response | null) | null = null;
let wide = true;

const base = (): LibraryView => ({
  paths: [
    { id: 'p_movies01', name: 'Movies', path: '/media/Movies', status: 'ok' },
    { id: 'p_4kmovie1', name: '4K Movies', path: '/media/4K Movies', status: 'ok' },
    { id: 'p_oldone01', name: 'Old drive', path: '/media/Old Drive', status: 'missing' },
  ],
  rules: [
    { id: 'r_first001', enabled: true, path_id: 'p_4kmovie1', released_within: null, resolution: ['2160p'], source: [] },
    { id: 'r_second01', enabled: true, path_id: 'p_movies01', released_within: { n: 6, unit: 'months' }, resolution: [], source: [] },
  ],
  fallback_path_id: 'p_movies01',
  path_map: [{ remote: '/data/torrents', local: '/downloads' }],
  transfer: 'copy',
});

function stubMatchMedia() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('min-width: 1024px') ? wide : false, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }));
}

function server() {
  return vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    const c = { url, method, body };
    calls.push(c);
    const custom = answer?.(c);
    if (custom) return custom;
    if (url.endsWith('/api/settings/library-paths') && method === 'GET') return res(state);
    if (url.endsWith('/api/settings/library-paths') && method === 'POST') {
      state = { ...state, paths: [...state.paths, { id: 'p_new00001', name: String(body!.name), path: String(body!.path), status: 'ok' }] };
      return res(state, 201);
    }
    if (url.endsWith('/api/settings/library-rules')) {
      state = { ...state, rules: (body!.rules as Rule[]).map((r, i) => ({ ...r, id: r.id || `r_new0000${i}` })),
        fallback_path_id: body!.fallback_path_id as string };
      return res(state);
    }
    if (url.endsWith('/api/settings/library-paths/mapping')) {
      state = { ...state, ...(body as Partial<LibraryView>) };
      return res(state);
    }
    if (url.includes('/api/settings/library-paths/') && method === 'DELETE') {
      state = { ...state, paths: state.paths.filter(p => !url.endsWith(p.id)) };
      return res(state);
    }
    if (url.includes('/api/settings/library-paths/') && method === 'PUT') {
      state = { ...state, paths: state.paths.map(p => (url.endsWith(p.id) ? { ...p, ...(body as object) } : p)) };
      return res(state);
    }
    if (url.includes('/api/settings/plex')) return res({ connected: false, server: '' });
    if (url.includes('/api/trackers/types')) return res({ types: [] });
    return res({ libraries: [] });
  });
}
const last = (pred: (c: Call) => boolean) => [...calls].reverse().find(pred);
const rulesPut = () => last(c => c.url.endsWith('/api/settings/library-rules'));
/** Visible text: <style> blocks dropped. */
const textOf = (el: Element) => { const c = el.cloneNode(true) as Element; c.querySelectorAll('style').forEach(x => x.remove()); return c.textContent; };
const toastText = async () => textOf(await screen.findByTestId('notice-toast'));
const choose = (testId: string, label: string) => {
  fireEvent.click(screen.getByTestId(testId));
  fireEvent.click(screen.getByRole('radio', { name: label }));
};
const summaries = () => screen.getAllByTestId('lp-rule-summary').map(e => e.textContent);

beforeEach(() => {
  state = base(); calls = []; answer = null; wide = true;
  stubMatchMedia();
  vi.stubGlobal('fetch', server());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); delete (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__; });

describe('Settings > Library paths', () => {
  it('lists paths with status, rules in order, the fallback, Copy and the path map; labels only', async () => {
    render(<LibraryPathsSettings wide />);
    const section = await screen.findByTestId('library-paths-section');
    const rows = screen.getAllByTestId('lp-path');
    expect(rows.map(r => r.getAttribute('data-status'))).toEqual(['ok', 'ok', 'missing']);
    expect(rows[2]!.textContent).toContain('Missing');
    // Status: green dot alone when OK; amber Read-only, red Missing (13px/500).
    expect(within(rows[0]!).queryByTestId('lp-status')).toBeNull();
    expect(within(rows[0]!).getByTestId('lp-status-dot').style.background).toBe('var(--color-success)');
    const missing = within(rows[2]!).getByTestId('lp-status');
    expect(missing.textContent).toBe('Missing');
    expect(missing.style.color).toBe('var(--color-danger)');
    expect(missing.style.fontSize).toBe('13px');
    expect(missing.style.fontWeight).toBe('500');
    expect(within(rows[2]!).getByTestId('lp-status-dot').style.background).toBe('var(--color-danger)');
    expect(summaries()).toEqual(['2160p', 'Past 6 months']);
    expect(screen.getAllByTestId('lp-rule-dest').map(e => e.textContent)).toEqual(['4K Movies', 'Movies']);
    expect(screen.getByTestId('lp-fallback-value').textContent).toBe('Movies');
    expect(screen.getByTestId('lp-transfer-copy').getAttribute('aria-checked')).toBe('true');
    expect(screen.getAllByTestId('lp-map-row')).toHaveLength(1);
    // No helper or explanatory text: just labels, names, paths and buttons.
    expect(textOf(section)).toBe(
      'PathsMovies/media/Movies4K Movies/media/4K MoviesOld drive/media/Old DriveMissingAdd path'
      + 'Rules2160p4K MoviesPast 6 monthsMoviesAdd ruleFallbackMovies'
      + 'TransferCopyHardlinkPath map/data/torrents/downloadsAdd mapping');
    // Only the new store's API: never the seeded media.* settings.
    expect(calls.every(c => c.url.includes('/api/settings/library-'))).toBe(true);
  });

  it('a fresh install shows Paths with Add path, Transfer and Path map only', async () => {
    state = { paths: [], rules: [], fallback_path_id: null, path_map: [], transfer: 'copy' };
    render(<LibraryPathsSettings wide />);
    await screen.findByTestId('lp-add-path');
    expect(screen.queryByTestId('lp-rules')).toBeNull();
    expect(screen.queryByTestId('lp-fallback')).toBeNull();
    expect(screen.getByTestId('lp-transfer')).toBeTruthy();
  });

  it('Add path posts the name and folder as typed (spaces kept) and shows server errors by field', async () => {
    answer = c => (c.method === 'POST' && c.body?.path === '/etc' ? res({ errors: { path: 'Not allowed' } }, 400) : null);
    render(<LibraryPathsSettings wide />);
    fireEvent.click(await screen.findByTestId('lp-add-path'));
    const save = screen.getByTestId('lp-save') as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByTestId('lp-name'), { target: { value: 'System' } });
    fireEvent.change(screen.getByTestId('lp-dir'), { target: { value: '/etc' } });
    fireEvent.click(save);
    expect((await screen.findByText('Not allowed')).id).toBe('lp-dir-error');
    expect(screen.getByTestId('lp-dir').getAttribute('aria-invalid')).toBe('true');
    fireEvent.change(screen.getByTestId('lp-name'), { target: { value: 'Recently released' } });
    fireEvent.change(screen.getByTestId('lp-dir'), { target: { value: ' /media/Recently Released ' } });
    fireEvent.click(save);
    await waitFor(() => expect(screen.getAllByTestId('lp-path')).toHaveLength(4));
    expect(last(c => c.method === 'POST')!.body).toEqual({ name: 'Recently released', path: '/media/Recently Released' });
    expect(screen.queryByTestId('lp-path-form')).toBeNull();
    expect(await toastText()).toBe('Added');
  });

  it('a path in use cannot be deleted: the server message shows and the path stays', async () => {
    answer = c => (c.method === 'DELETE' ? res({ error: 'In use by a rule or the fallback' }, 409) : null);
    render(<LibraryPathsSettings wide />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Movies' }));
    fireEvent.click(screen.getByTestId('lp-delete'));
    expect(await toastText()).toBe('In use by a rule or the fallback');
    expect(screen.getAllByTestId('lp-path')).toHaveLength(2);
    expect(screen.getByTestId('lp-path-form')).toBeTruthy();
    expect(state.paths).toHaveLength(3);
    expect(last(c => c.method === 'DELETE')!.url).toMatch(/\/api\/settings\/library-paths\/p_movies01$/);
  });

  it('the switch saves instantly with the fallback', async () => {
    render(<LibraryPathsSettings wide />);
    await screen.findAllByTestId('lp-rule');
    fireEvent.click(screen.getByRole('switch', { name: 'Rule 2' }));
    await waitFor(() => expect(rulesPut()).toBeTruthy());
    const body = rulesPut()!.body!;
    expect((body.rules as Rule[]).map(r => r.enabled)).toEqual([true, false]);
    expect(body.fallback_path_id).toBe('p_movies01');
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Rule 2' }).getAttribute('aria-checked')).toBe('false'));
  });

  it('arrow keys on the handle reorder and keep focus; a failed save reverts with a toast', async () => {
    render(<LibraryPathsSettings wide />);
    await screen.findAllByTestId('lp-rule');
    const h = screen.getByRole('button', { name: 'Reorder rule 1' });
    h.focus();
    fireEvent.keyDown(h, { key: 'ArrowDown' });
    expect(summaries()).toEqual(['Past 6 months', '2160p']);
    await waitFor(() => expect((rulesPut()!.body!.rules as Rule[]).map(r => r.id)).toEqual(['r_second01', 'r_first001']));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Reorder rule 2' }));
    answer = c => (c.url.endsWith('/library-rules') ? res({ error: 'Nope' }, 500) : null);
    fireEvent.keyDown(screen.getByRole('button', { name: 'Reorder rule 2' }), { key: 'ArrowUp' });
    expect(summaries()).toEqual(['2160p', 'Past 6 months']);
    await waitFor(() => expect(summaries()).toEqual(['Past 6 months', '2160p']));
    expect(await toastText()).toBe('Nope');
  });

  it('dragging the handle past a row moves it', async () => {
    render(<LibraryPathsSettings wide />);
    await screen.findAllByTestId('lp-rule');
    const h = screen.getByRole('button', { name: 'Reorder rule 1' });
    fireEvent.pointerDown(h, { clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(h, { clientY: 170, pointerId: 1 });
    expect(screen.getAllByTestId('lp-rule')[0]!.getAttribute('data-lifted')).toBe('true');
    fireEvent.pointerUp(h, { clientY: 170, pointerId: 1 });
    await waitFor(() => expect((rulesPut()!.body!.rules as Rule[]).map(r => r.id)).toEqual(['r_second01', 'r_first001']));
  });

  it('Add rule: destination, released within N years, resolution and source chips', async () => {
    render(<LibraryPathsSettings wide />);
    fireEvent.click(await screen.findByTestId('lp-add-rule'));
    const save = screen.getByTestId('lp-save') as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    choose('lp-dest', 'Old drive');
    choose('lp-released', 'Years');
    fireEvent.change(screen.getByTestId('lp-released-n'), { target: { value: '2y' } });
    expect((screen.getByTestId('lp-released-n') as HTMLInputElement).value).toBe('2');
    fireEvent.click(screen.getByTestId('lp-chip-1080p'));
    fireEvent.click(screen.getByTestId('lp-chip-remux'));
    fireEvent.click(screen.getByTestId('lp-chip-web'));
    fireEvent.click(screen.getByTestId('lp-chip-web'));
    expect(screen.getByTestId('lp-chip-1080p').getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(save);
    await waitFor(() => expect(summaries()).toHaveLength(3));
    const added = (rulesPut()!.body!.rules as Rule[])[2];
    expect(added).toEqual({ enabled: true, path_id: 'p_oldone01', released_within: { n: 2, unit: 'years' }, resolution: ['1080p'], source: ['remux'] });
    expect(summaries()[2]).toBe('Past 2 years · 1080p · Remux');
  });

  it('rule errors from the server show on the field; Delete removes the rule', async () => {
    answer = c => (c.url.endsWith('/library-rules') && (c.body!.rules as Rule[])[0]?.released_within?.n === 999
      ? res({ errors: { 'rules.0.released_within': 'Invalid' } }, 400) : null);
    render(<LibraryPathsSettings wide />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit rule 1' }));
    choose('lp-released', 'Months');
    fireEvent.change(screen.getByTestId('lp-released-n'), { target: { value: '999' } });
    fireEvent.click(screen.getByTestId('lp-save'));
    expect(await screen.findByText('Invalid')).toBeTruthy();
    fireEvent.click(screen.getByTestId('lp-delete'));
    await waitFor(() => expect(summaries()).toEqual(['Past 6 months']));
  });

  it('the fallback picker saves the chosen path; a missing fallback shows Required', async () => {
    state = { ...base(), rules: [], fallback_path_id: null };
    answer = c => (c.url.endsWith('/library-rules') && !c.body!.fallback_path_id
      ? res({ errors: { fallback_path_id: 'Required' } }, 400) : null);
    render(<LibraryPathsSettings wide />);
    fireEvent.click(await screen.findByTestId('lp-add-rule'));
    choose('lp-dest', '4K Movies');
    fireEvent.click(screen.getByTestId('lp-save'));
    expect((await within(screen.getByTestId('lp-fallback')).findByRole('alert')).textContent).toBe('Required');
    fireEvent.click(screen.getByTestId('lp-fallback-toggle'));
    fireEvent.click(screen.getByRole('radio', { name: 'Movies' }));
    await waitFor(() => expect(screen.getByTestId('lp-fallback-value').textContent).toBe('Movies'));
    expect(rulesPut()!.body).toEqual({ rules: [], fallback_path_id: 'p_movies01' });
    expect(within(screen.getByTestId('lp-fallback')).queryByRole('alert')).toBeNull();
  });

  it('one path is the fallback without asking', async () => {
    state = { paths: [base().paths[0]!], rules: [], fallback_path_id: null, path_map: [], transfer: 'copy' };
    render(<LibraryPathsSettings wide />);
    fireEvent.click(await screen.findByTestId('lp-add-rule'));
    expect(screen.queryByTestId('lp-fallback')).toBeNull();
    choose('lp-dest', 'Movies');
    fireEvent.click(screen.getByTestId('lp-save'));
    await waitFor(() => expect(rulesPut()!.body!.fallback_path_id).toBe('p_movies01'));
  });

  it('Hardlink saves the transfer; a failure reverts to Copy', async () => {
    render(<LibraryPathsSettings wide />);
    fireEvent.click(await screen.findByTestId('lp-transfer-hardlink'));
    await waitFor(() => expect(last(c => c.url.endsWith('/mapping'))!.body).toEqual({ transfer: 'hardlink' }));
    expect(screen.getByTestId('lp-transfer-hardlink').getAttribute('aria-checked')).toBe('true');
    answer = c => (c.url.endsWith('/mapping') ? res({ errors: { transfer: 'Invalid' } }, 400) : null);
    fireEvent.click(screen.getByTestId('lp-transfer-copy'));
    await waitFor(() => expect(screen.getByTestId('lp-transfer-hardlink').getAttribute('aria-checked')).toBe('true'));
  });

  it('path map: add a row, show row errors, remove a row', async () => {
    answer = c => (c.url.endsWith('/mapping') && (c.body!.path_map as unknown[] | undefined)?.length === 2
      && (c.body!.path_map as { local: string }[])[1]!.local === '/nope'
      ? res({ errors: { 'path_map.1.local': 'Folder not found' } }, 400) : null);
    render(<LibraryPathsSettings wide />);
    fireEvent.click(await screen.findByTestId('lp-add-map'));
    fireEvent.change(screen.getByTestId('lp-remote'), { target: { value: '/srv/qb' } });
    fireEvent.change(screen.getByTestId('lp-local'), { target: { value: '/nope' } });
    fireEvent.click(screen.getByTestId('lp-save'));
    await waitFor(() => expect(document.getElementById('lp-local-error')?.textContent).toBe('Folder not found'));
    fireEvent.change(screen.getByTestId('lp-local'), { target: { value: '/downloads/other' } });
    fireEvent.click(screen.getByTestId('lp-save'));
    await waitFor(() => expect(screen.getAllByTestId('lp-map-row')).toHaveLength(2));
    fireEvent.click(screen.getByRole('button', { name: 'Remove /data/torrents' }));
    await waitFor(() => expect(screen.getAllByTestId('lp-map-row')).toHaveLength(1));
    expect(last(c => c.url.endsWith('/mapping'))!.body).toEqual({ path_map: [{ remote: '/srv/qb', local: '/downloads/other' }] });
  });

  it('load failure shows a short message', async () => {
    answer = c => (c.method === 'GET' ? res({ error: 'Owner only' }, 403) : null);
    render(<LibraryPathsSettings wide />);
    expect((await screen.findByRole('alert')).textContent).toBe("Couldn't load library paths");
  });

  it('designer polish: chips, row pickers without a native select, desktop type sizes, Delete at the left', async () => {
    render(<LibraryPathsSettings wide />);
    const title = (await screen.findAllByTestId('lp-rule-summary'))[0]!;
    expect(title.style.fontSize).toBe('15px');
    expect(title.style.fontWeight).toBe('500');
    const sub = screen.getAllByTestId('lp-rule-dest')[0]!;
    expect(sub.style.fontSize).toBe('13px');
    expect(sub.style.color).toBe('rgba(255, 255, 255, 0.5)');
    fireEvent.click(screen.getByRole('button', { name: 'Edit rule 2' }));
    const form = screen.getByTestId('lp-rule-form');
    expect(form.querySelector('select')).toBeNull();
    const dest = screen.getByTestId('lp-dest');
    expect(dest.style.height).toBe('44px');
    expect((dest.parentElement as HTMLElement).style.borderRadius).toBe('12px');
    expect((dest.parentElement as HTMLElement).style.background).toBe('rgba(255, 255, 255, 0.06)');
    expect(screen.getByTestId('lp-released').textContent).toBe('Months');
    const off = screen.getByTestId('lp-chip-2160p');
    expect(off.getAttribute('aria-pressed')).toBe('false');
    expect(off.style.height).toBe('36px');
    expect(off.style.borderRadius).toBe('999px');
    expect(off.style.padding).toBe('0px 14px');
    expect(off.style.fontSize).toBe('14px');
    expect(off.style.background).toBe('rgba(255, 255, 255, 0.06)');
    expect(off.style.border).toBe('1px solid rgba(255, 255, 255, 0.1)');
    expect(off.style.color).toBe('rgba(255, 255, 255, 0.7)');
    fireEvent.click(off);
    expect(off.style.background).toBe('rgba(59, 130, 246, 0.18)');
    expect(off.style.border).toBe('1px solid var(--color-accent)');   // #3b82f6
    expect(off.style.color).toBe('var(--color-white-pure)');
    const buttons = [...form.querySelectorAll('button')].filter(b => ['Delete', 'Cancel', 'Save'].includes(b.textContent || ''));
    expect(buttons.map(b => b.textContent)).toEqual(['Delete', 'Cancel', 'Save']);
    expect(screen.getByTestId('lp-delete').style.color).toBe('var(--color-danger-text)');
  });

  it('phone: chips are 44px and titles 16px', async () => {
    render(<LibraryPathsSettings wide={false} />);
    expect((await screen.findAllByTestId('lp-rule-summary'))[0]!.style.fontSize).toBe('16px');
    fireEvent.click(screen.getByRole('button', { name: 'Edit rule 1' }));
    expect(screen.getByTestId('lp-chip-2160p').style.height).toBe('44px');
  });

  it('summaries are American English', () => {
    const r = (x: Partial<Rule>): Rule => ({ enabled: true, path_id: 'p', released_within: null, resolution: [], source: [], ...x });
    expect(ruleSummary(r({}))).toBe('All movies');
    expect(ruleSummary(r({ released_within: { n: 1, unit: 'years' } }))).toBe('Past 1 year');
    expect(ruleSummary(r({ released_within: { n: 1, unit: 'months' }, resolution: ['sd'], source: ['bluray', 'hdtv'] })))
      .toBe('Past 1 month · SD · Blu-ray, HDTV');
  });
});

function Where() { return <p data-testid="where">{useLocation().pathname}</p>; }
const renderAt = (path: string) => render(<MemoryRouter initialEntries={[path]}><Routes>
  <Route path="/settings" element={<SettingsPage />} />
  <Route path="/settings/:section" element={<SettingsPage />} />
</Routes><Where /></MemoryRouter>);

describe('Settings wiring for Library paths', () => {
  beforeEach(() => { auth.user.is_admin = true; });

  it('minimal build, owner: after Trackers, before TV; the section loads', async () => {
    (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    renderAt('/settings/library-paths');
    const nav = screen.getByTestId('settings-nav');
    await waitFor(() => expect(within(nav).getAllByRole('link').map(a => a.textContent).slice(-2)).toEqual(['Library paths', 'TV']));
    expect(within(nav).getByRole('link', { name: 'Library paths' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('heading', { level: 2, name: 'Library paths' })).toBeTruthy();
    expect(await screen.findByTestId('library-paths-section')).toBeTruthy();
  });

  it('phone: a Library paths row pushes the section in', async () => {
    (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    wide = false; stubMatchMedia();
    renderAt('/settings');
    const list = await screen.findByTestId('settings-list');
    await act(async () => { await waitFor(() => expect(within(list).getByRole('link', { name: 'Library paths' })).toBeTruthy()); });
    fireEvent.click(within(list).getByRole('link', { name: 'Library paths' }));
    expect(await screen.findByTestId('library-paths-section')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Library paths' })).toBeTruthy();
  });

  it('not minimal: hidden, and the URL goes back to Home screen', async () => {
    renderAt('/settings/library-paths');
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings/home-screen'));
    await waitFor(() => expect(within(screen.getByTestId('settings-nav')).getAllByRole('link').length).toBeGreaterThan(2));
    expect(within(screen.getByTestId('settings-nav')).queryByRole('link', { name: 'Library paths' })).toBeNull();
    expect(calls.some(c => c.url.includes('library-'))).toBe(false);
  });

  it('not the owner: hidden even on a minimal build', async () => {
    (window as { __LUMA_MINIMAL__?: boolean }).__LUMA_MINIMAL__ = true;
    answer = c => (c.url.includes('/api/settings/plex') ? res({ error: 'Owner only' }, 403) : null);
    renderAt('/settings/library-paths');
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/settings/home-screen'));
    expect(screen.queryByText('Library paths')).toBeNull();
    expect(calls.some(c => c.url.includes('library-'))).toBe(false);
  });
});
