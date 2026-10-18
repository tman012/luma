/** QA (Luma Tester) dcb9cc1: in minimal builds (never implicit) a "Sign in to
 * choose libraries" / "Sign in to edit trackers" button after the session
 * expired navigates to /login with {from, signIn: true} while AuthContext still
 * says authenticated, and LoginPage bounces straight back (seen live: /login ->
 * /settings/tv -> /settings/home-screen, no form). Expected: the form shows.
 * Fixed: LoginPage honours state.signIn in every mode. */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

const auth = { implicit: false, authenticated: true, loading: false, login: vi.fn(), needsStart: false, pinUnlock: false, signupOpen: false };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
import { LoginPage } from '@/pages/LoginPage';

afterEach(() => { cleanup(); });

describe('QA dcb9cc1: sign-in button after an expired session (minimal)', () => {
  it('LoginPage with state.signIn shows the form instead of bouncing back', async () => {
    render(
      <MemoryRouter initialEntries={[{ pathname: '/login', state: { from: '/settings/tv', signIn: true } }]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/settings/tv" element={<div data-testid="bounced">bounced</div>} />
        </Routes>
      </MemoryRouter>,
    );
    await new Promise(r => setTimeout(r, 50));
    expect(screen.queryByTestId('bounced')).toBeNull();
    expect(document.querySelector('input[type=password]')).not.toBeNull();
  });

  it('after signing in it goes back to where the button was', async () => {
    auth.login.mockResolvedValue({ success: true });
    render(
      <MemoryRouter initialEntries={[{ pathname: '/login', state: { from: '/settings/tv', signIn: true } }]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/settings/tv" element={<div data-testid="back">back</div>} />
        </Routes>
      </MemoryRouter>,
    );
    const email = document.querySelector('input[type=email], input[name=email], input[autocomplete=username]') as HTMLInputElement;
    const pin = document.querySelector('input[type=password]') as HTMLInputElement;
    fireEvent.change(email, { target: { value: 'taylor@example.com' } });
    fireEvent.change(pin, { target: { value: '1234' } });
    fireEvent.submit(pin.closest('form')!);
    expect(await screen.findByTestId('back')).toBeTruthy();
    expect(auth.login).toHaveBeenCalledWith('taylor@example.com', '1234');
  });

  it('without signIn a signed-in visit to /login still goes back', async () => {
    render(
      <MemoryRouter initialEntries={[{ pathname: '/login', state: { from: '/settings/tv' } }]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/settings/tv" element={<div data-testid="bounced">bounced</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId('bounced')).toBeTruthy();
  });
});
