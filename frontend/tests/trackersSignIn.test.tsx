// Tester ab1c841 #2: the Trackers UI acts on a 401 that carries sign_in.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TrackersSettings, useTrackersAccess } from '@/components/trackers/TrackersSettings';
import TYPES from './fixtures/trackerTypes.json';

const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
const SIGN_IN = { error: 'Authentication required', sign_in: true };
const tracker = { id: 1, name: 'Idx', type: (TYPES as { id: string }[])[0]!.id, type_name: 'X', protocol: 'torrent', enabled: true,
  url: 'https://idx.example', categories: [], priority: 25, status: { state: 'untested', message: '', checked_at: null } };

function Where() { const l = useLocation(); return <p data-testid="where">{l.pathname}|{JSON.stringify(l.state)}</p>; }
function renderAt(path: string, wide = true) {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/settings/trackers/*" element={<TrackersSettings wide={wide} />} />
    <Route path="/login" element={<p>login page</p>} />
  </Routes><Where /></MemoryRouter>);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Trackers sign-in', () => {
  it.each([true, false])('a load 401 with sign_in shows a Sign in button (wide=%s) that goes to sign-in and back', async wide => {
    vi.stubGlobal('fetch', vi.fn(async () => res(SIGN_IN, 401)));
    renderAt('/settings/trackers', wide);
    const btn = await screen.findByTestId('trackers-sign-in');
    expect(btn.textContent).toBe('Sign in to edit trackers');
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/login|{"from":"/settings/trackers","signIn":true}'));
  });

  it('a 401 without sign_in still renders nothing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res({ error: 'Authentication required' }, 401)));
    const { container } = renderAt('/settings/trackers');
    await waitFor(() => expect(container.querySelector('[data-testid="trackers-section"]')).toBeNull());
    expect(screen.queryByTestId('trackers-sign-in')).toBeNull();
  });

  it('a session that expires mid-edit: Save answers 401 sign_in and the Sign in button appears', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input); const method = (init?.method || 'GET').toUpperCase();
      if (url.endsWith('/types')) return res({ types: TYPES });
      if (url.endsWith('/api/trackers') && method === 'GET') return res({ trackers: [tracker] });
      return res(SIGN_IN, 401);
    }));
    renderAt('/settings/trackers');
    await screen.findByTestId('trackers-card');
    expect(screen.queryByTestId('trackers-sign-in')).toBeNull();
    fireEvent.click(screen.getByRole('switch'));
    expect((await screen.findByTestId('trackers-sign-in')).textContent).toBe('Sign in to edit trackers');
  });

  it('useTrackersAccess keeps Trackers listed on a 401 with sign_in', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res(SIGN_IN, 401)));
    let seen: boolean | null = null;
    function Probe() { seen = useTrackersAccess(true); return null; }
    render(<Probe />);
    await waitFor(() => expect(seen).toBe(true));
  });
});
