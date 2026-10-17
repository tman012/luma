/** PM ruling: deleting files needs the owner signed in with a session, even
 * with LAN sign-in off. The storage sheet offers a sign-in instead. */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const auth = { implicit: false, authenticated: true, loading: false, login: vi.fn(), needsStart: false, pinUnlock: false, signupOpen: false };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('@/contexts/NotificationsContext', () => ({ useNotificationsContext: () => ({ downloads: [] }) }));

import { SIGN_IN_TO_FREE, StoragePill } from '@/components/storage/StoragePill';
import { LoginPage } from '@/pages/LoginPage';

const usage = { path: '/downloads', available: true, total_bytes: 4e12, used_bytes: 3e12, free_bytes: 1e12, cleanup_target_bytes: 100 * 1024 ** 3 };
let cleanupStatus = 200;
let posts = 0;
const res = (body: unknown, status = 200) => ({ ok: status < 300, status, statusText: '', json: async () => body }) as Response;

function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname}|{JSON.stringify(loc.state)}</div>;
}

function renderPill() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<StoragePill />} />
        <Route path="/login" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  auth.implicit = false; cleanupStatus = 200; posts = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).endsWith('/api/storage/downloads/cleanup')) {
      posts += 1;
      return cleanupStatus === 401
        ? res({ error: 'Sign in first', sign_in: true }, 401)
        : res({ success: true, freed_bytes: 5, deleted_count: 1, deleted: [], failed: [] });
    }
    if (String(url).endsWith('/api/storage/downloads')) return res(usage);
    return res({}, init ? 200 : 200);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openSheet() {
  renderPill();
  await waitFor(() => expect(screen.getByRole('button', { name: /free/ })).toBeTruthy());
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /free/ })); });
}

describe('Storage cleanup sign-in', () => {
  it('the implicit LAN owner gets a sign-in prompt, never the delete', async () => {
    auth.implicit = true;
    await openSheet();
    const btn = await screen.findByTestId('storage-sign-in');
    expect(btn.textContent).toBe(SIGN_IN_TO_FREE);
    expect(SIGN_IN_TO_FREE).toBe('Sign in to free up space');
    expect(screen.queryByText(/Free up 100 GB/)).toBeNull();
    expect(screen.queryByText(/Deletes the oldest/)).toBeNull();
    await act(async () => { fireEvent.click(btn); });
    expect(screen.getByTestId('where').textContent).toBe('/login|{"from":"/","signIn":true}');
    expect(posts).toBe(0);
  });

  it('a signed-in owner sees the delete', async () => {
    await openSheet();
    expect(await screen.findByText(/Free up 100 GB/)).toBeTruthy();
    expect(screen.queryByTestId('storage-sign-in')).toBeNull();
  });

  it('a 401 from the server turns into the sign-in prompt', async () => {
    cleanupStatus = 401;
    await openSheet();
    await act(async () => { fireEvent.click(await screen.findByText(/Free up 100 GB/)); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Delete 100 GB/ })); });
    expect((await screen.findByTestId('storage-sign-in')).textContent).toBe('Sign in to free up space');
    expect(posts).toBe(1);
    expect(screen.queryByText(/API error/)).toBeNull();
  });
});

describe('LoginPage for an implicit LAN owner', () => {
  const renderLogin = (state: unknown) => render(
    <MemoryRouter initialEntries={[{ pathname: '/login', state }]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<div data-testid="home" />} />
      </Routes>
    </MemoryRouter>,
  );

  it('shows the form when sent to sign in for an action', () => {
    auth.implicit = true;
    renderLogin({ from: '/', signIn: true });
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Sign in');
    expect(screen.queryByTestId('home')).toBeNull();
  });

  it('otherwise still goes home, as today', async () => {
    auth.implicit = true;
    renderLogin(null);
    expect(await screen.findByTestId('home')).toBeTruthy();
  });
});
