/** LibrarySelector: the reusable "pick Library paths for a section" list
 * (TV now; Radarr-style Movies later), and its API helpers. */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LIBRARY_PATHS_HREF, LibrarySelector, SIGN_IN_TO_CHOOSE } from '@/components/library/LibrarySelector';
import { WideCtx } from '@/components/library/LibraryPathsSettings';
import { loadSelection, saveSelection, toggled } from '@/components/library/librarySelectionApi';

const URL = '/api/settings/movies/libraries';      // any section's URL: nothing here is TV-specific
const A = { id: 'p_0000000a', name: 'TV', path: '/media/TV', status: 'ok' };
const B = { id: 'p_0000000b', name: 'Kids', path: '/media/Kids TV', status: 'read_only' };
const C = { id: 'p_0000000c', name: 'Old', path: '/media/Old', status: 'missing' };
const res = (body: unknown, status = 200) => ({ ok: status < 300, status, json: async () => body }) as Response;
let paths: typeof A[];
let selected: string[];
let putReply: Response | null;
const puts: unknown[] = [];

beforeEach(() => {
  paths = [A, B, C];
  selected = [A.id];
  putReply = null;
  puts.length = 0;
  vi.stubGlobal('fetch', vi.fn(async (u: string, init?: RequestInit) => {
    if (String(u) !== URL) return res({ error: 'Not found' }, 404);
    if (init?.method === 'PUT') {
      const body = JSON.parse(String(init.body));
      puts.push(body);
      if (putReply) return putReply;
      selected = body.selected;
    }
    return res({ selected, libraries: paths.map(p => ({ ...p, selected: selected.includes(p.id) })) });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const mount = (onChange = vi.fn(), wide = true) => {
  render(
    <MemoryRouter initialEntries={['/settings/tv']}>
      <WideCtx.Provider value={wide}>
        <Routes>
          <Route path="/settings/tv" element={<LibrarySelector url={URL} label="Libraries" testId="sel" onChange={onChange} />} />
          <Route path={LIBRARY_PATHS_HREF} element={<div data-testid="at-library-paths" />} />
          <Route path="/login" element={<div data-testid="at-login" />} />
        </Routes>
      </WideCtx.Provider>
    </MemoryRouter>,
  );
  return onChange;
};

describe('LibrarySelector', () => {
  it('lists every Library path with its state; tapping selects and deselects', async () => {
    const onChange = mount();
    const rows = await screen.findAllByTestId('lib-select-row');
    expect(rows.map(r => r.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
    expect(rows.map(r => r.getAttribute('role'))).toEqual(['checkbox', 'checkbox', 'checkbox']);
    expect(screen.getAllByTestId('lib-select-status').map(s => s.textContent)).toEqual(['Read-only', 'Missing']);
    expect(onChange).toHaveBeenLastCalledWith([{ ...A, selected: true }]);
    fireEvent.click(rows[1]!);
    await waitFor(() => expect(screen.getAllByTestId('lib-select-row')[1]!.getAttribute('aria-checked')).toBe('true'));
    fireEvent.click(screen.getAllByTestId('lib-select-row')[0]!);
    await waitFor(() => expect(screen.getAllByTestId('lib-select-row')[0]!.getAttribute('aria-checked')).toBe('false'));
    expect(puts).toEqual([{ selected: [A.id, B.id] }, { selected: [B.id] }]);
    expect(onChange).toHaveBeenLastCalledWith([{ ...B, selected: true }]);
  });

  it('nothing selected: just the list', async () => {
    selected = [];
    mount();
    const rows = await screen.findAllByTestId('lib-select-row');
    expect(rows.every(r => r.getAttribute('aria-checked') === 'false')).toBe(true);
    expect(screen.getByTestId('sel').textContent).toBe('LibrariesTV/media/TVKids/media/Kids TVRead-onlyOld/media/OldMissing');
    expect(screen.queryByTestId('lib-select-link')).toBeNull();
  });

  it('no Library paths: only a link-style button to Library paths', async () => {
    paths = [];
    selected = [];
    mount();
    const link = await screen.findByTestId('lib-select-link');
    expect(link.textContent).toBe('Library paths');
    expect(link.getAttribute('href')).toBe('/settings/library-paths');
    expect(screen.getByTestId('sel').textContent).toBe('LibrariesLibrary paths');
    expect(screen.queryByRole('group')).toBeNull();
    fireEvent.click(link);
    await screen.findByTestId('at-library-paths');
  });

  it('a 401 with sign_in shows a sign-in button; nothing changes', async () => {
    putReply = res({ error: 'Sign in first', sign_in: true }, 401);
    mount(vi.fn(), false);
    fireEvent.click((await screen.findAllByTestId('lib-select-row'))[1]!);
    const btn = await screen.findByTestId('lib-select-sign-in');
    expect(btn.textContent).toBe(SIGN_IN_TO_CHOOSE);
    expect(btn.style.width).toBe('100%');
    expect(screen.getAllByTestId('lib-select-row')[1]!.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(btn);
    await screen.findByTestId('at-login');
  });

  it('a 403 shows its message, never a sign-in button', async () => {
    putReply = res({ error: 'Owner only' }, 403);
    mount();
    fireEvent.click((await screen.findAllByTestId('lib-select-row'))[1]!);
    await screen.findByText('Owner only');
    expect(screen.queryByTestId('lib-select-sign-in')).toBeNull();
  });

  it('a 401 without sign_in is just a message', async () => {
    putReply = res({ error: 'Sign in first' }, 401);
    mount();
    fireEvent.click((await screen.findAllByTestId('lib-select-row'))[1]!);
    await screen.findByText('Sign in first');
    expect(screen.queryByTestId('lib-select-sign-in')).toBeNull();
  });

  it('a field error from the server shows as the message', async () => {
    putReply = res({ errors: { selected: 'Not a library path' } }, 400);
    mount();
    fireEvent.click((await screen.findAllByTestId('lib-select-row'))[2]!);
    await screen.findByText('Not a library path');
  });

  it('no helper text; sentence case, American English', () => {
    const src = readFileSync(resolve(__dirname, '../src/components/library/LibrarySelector.tsx'), 'utf8');
    expect(src).not.toMatch(/hint=|note=|helper=|colour|Library Paths|Sign In/);
    expect(SIGN_IN_TO_CHOOSE).toBe('Sign in to choose libraries');
  });
});

describe('librarySelectionApi', () => {
  it('loads and saves any section URL', async () => {
    expect(await loadSelection(URL)).toMatchObject({ ok: true, data: { selected: [A.id] } });
    expect(await saveSelection(URL, [C.id])).toMatchObject({ ok: true, data: { selected: [C.id] } });
    expect(await loadSelection('/api/nope')).toMatchObject({ ok: false, status: 404 });
  });

  it('toggles an id', () => {
    expect(toggled(['a'], 'b')).toEqual(['a', 'b']);
    expect(toggled(['a', 'b'], 'a')).toEqual(['b']);
  });
});
