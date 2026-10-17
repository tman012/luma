import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const responses: Record<string, () => { ok: boolean; body: unknown }> = {};
vi.mock('@/apiClient', () => ({
  apiFetch: vi.fn(async (url: string) => {
    const r = (responses[url] ?? (() => ({ ok: false, body: {} })))();
    return { ok: r.ok, status: r.ok ? 200 : 409, json: async () => r.body };
  }),
}));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ refreshUser: vi.fn(), authenticated: false, startInProgress: false, loading: false }),
}));
import { StartPage } from '@/pages/StartPage';

function fakePopup() {
  const popup = { close: vi.fn(), location: { href: '' } };
  vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
  return popup;
}

async function startSignIn() {
  const view = render(<MemoryRouter><StartPage /></MemoryRouter>);
  const btn = await screen.findByRole('button', { name: /Continue with Plex/ });
  await act(async () => { fireEvent.click(btn); });
  await screen.findByText('Waiting for Plex…');
  return view;
}

describe('Plex pop-up', () => {
  beforeEach(() => {
    responses['/api/start/status'] = () => ({ ok: true, body: { needs_start: true, in_progress: false } });
    responses['/api/start/plex/pin'] = () => ({ ok: true, body: { auth_url: 'https://plex.example/auth', status: 'waiting' } });
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

  it('closes when the page unmounts', async () => {
    const popup = fakePopup();
    const view = await startSignIn();
    expect(popup.location.href).toBe('https://plex.example/auth');
    view.unmount();
    expect(popup.close).toHaveBeenCalled();
  });

  it('closes when the server asks to start over', async () => {
    const popup = fakePopup();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await startSignIn();
    responses['/api/start/plex/pin'] = () => ({ ok: false, body: { restart: true } });
    await act(async () => { await vi.advanceTimersByTimeAsync(1600); });
    expect(popup.close).toHaveBeenCalled();
    expect(await screen.findByRole('button', { name: /Continue with Plex/ })).toBeTruthy();
  });
});
