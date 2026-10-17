/** PM prod hotfix: a real Downloads Guard run needs the owner signed in with a
 * session. On 401 the card shows the sign-in prompt instead of deleting. */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const auth = { implicit: false, authenticated: true, loading: false, login: vi.fn(), needsStart: false, pinUnlock: false, signupOpen: false };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
const settingsStub = {
  settings: {}, savedSettings: {}, schema: {}, loading: false, getByPath: () => undefined, setByPath: () => {},
  saveCard: async () => true, resetCard: () => {}, isCardDirty: () => false, needsSignIn: () => false,
  testConnection: async () => ({ success: true, message: '' }), reload: async () => {}, toasts: [], showToast: () => {}, dismissToast: () => {},
};
vi.mock('@/pages/settings/useSettings', () => ({ useSettings: () => settingsStub }));

import { DownloadsGuardCard } from '@/pages/settings/DownloadsGuardCard';
import { LoginPage } from '@/pages/LoginPage';
import { triggerDownloadsGuardRun } from '@/services/settingsApi';

const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let runStatus = 200;
const runs: unknown[] = [];

function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname}|{JSON.stringify(loc.state)}</div>;
}

beforeEach(() => {
  runStatus = 200; runs.length = 0; auth.implicit = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.endsWith('/api/downloads-guard/run')) {
      runs.push(JSON.parse(String(init?.body)));
      if (runStatus === 401) return res({ error: 'Sign in first', sign_in: true }, 401);
      if (runStatus === 403) return res({ error: 'Owner only' }, 403);
      return res({ ok: true, dry_run: false });
    }
    if (u.endsWith('/api/downloads-guard/status')) return res({ status: { running: false }, log: [] });
    if (u.endsWith('/api/downloads-guard/trackers')) return res({ available: false, trackers: [], unknown_hosts: [], unclassified_count: 0, unclassified_bytes: 0, total_count: 0 });
    return res({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('triggerDownloadsGuardRun', () => {
  it('a 401 asks for sign-in, a 403 is an owner-only error', async () => {
    runStatus = 401;
    expect(await triggerDownloadsGuardRun(false)).toEqual({ ok: false, dry_run: false, signIn: true });
    runStatus = 403;
    await expect(triggerDownloadsGuardRun(false)).rejects.toThrow('Only the owner can delete downloads');
    runStatus = 200;
    expect(await triggerDownloadsGuardRun(true)).toEqual({ ok: true, dry_run: false });
  });
});

describe('Downloads Guard card', () => {
  const renderCard = () => render(
    <MemoryRouter initialEntries={['/settings/automation']}>
      <Routes>
        <Route path="/settings/automation" element={<DownloadsGuardCard />} />
        <Route path="/login" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );

  async function confirmRun() {
    renderCard();
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: /Clean up now/ })); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /delete permanently/ })); });
  }

  it('on 401 shows "Sign in to clean up" and it opens the sign-in form for this page', async () => {
    runStatus = 401;
    await confirmRun();
    const b = await screen.findByRole('button', { name: 'Sign in to clean up' });
    expect(runs).toEqual([{ dry_run: false }]);
    expect(screen.queryByText(/Cleanup started/)).toBeNull();
    expect(screen.queryByText(/\b401\b|HTTP/)).toBeNull();
    fireEvent.click(b);
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/login|{"signIn":true,"from":"/settings/automation"}'));
  });

  it('a signed-in owner run starts as before', async () => {
    await confirmRun();
    expect(await screen.findByText(/Cleanup started/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Sign in to clean up' })).toBeNull();
  });

  it('a 403 shows the owner-only message, no sign-in prompt', async () => {
    runStatus = 403;
    await confirmRun();
    expect(await screen.findByText('Only the owner can delete downloads')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Sign in to clean up' })).toBeNull();
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
    expect(screen.queryByTestId('home')).toBeNull();
  });

  it('otherwise still goes home, as today', async () => {
    auth.implicit = true;
    renderLogin(null);
    expect(await screen.findByTestId('home')).toBeTruthy();
  });
});
