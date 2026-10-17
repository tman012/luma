import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const login = vi.fn(async () => ({ success: false, error: 'Invalid email, username, or password' }));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ login, authenticated: false, loading: false, needsStart: false, pinUnlock: false, signupOpen: false }),
}));
import { LoginPage } from '@/pages/LoginPage';
import { LOGO_SRC } from '@/components/start/StartUI';

const src = readFileSync(resolve(__dirname, '../src/pages/LoginPage.tsx'), 'utf8');

describe('LoginPage', () => {
  afterEach(cleanup);

  it('renders wordmark, 32px gap, heading, field and blue button on navy', () => {
    const { container } = render(<MemoryRouter><LoginPage /></MemoryRouter>);
    const logo = screen.getByAltText('Luma') as HTMLImageElement;
    expect(logo.getAttribute('src')).toContain(LOGO_SRC);
    const gap = logo.nextElementSibling as HTMLElement;
    expect(gap.style.height).toBe('32px');
    expect(gap.nextElementSibling?.tagName).toBe('H1');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Sign in');
    expect(screen.getByText('Email or username')).toBeTruthy();
    const input = container.querySelector('input[autocomplete="username"]') as HTMLInputElement;
    expect(input.type).toBe('text');
    expect(input.style.borderRadius).toBe('12px');
    fireEvent.change(input, { target: { value: 'taylor' } });
    fireEvent.change(container.querySelector('input[type="password"]')!, { target: { value: 'x' } });
    const btn = screen.getByRole('button', { name: 'Sign in' }) as HTMLButtonElement;
    expect(btn.style.background).toMatch(/rgb\(59, 130, 246\)|#3b82f6/);
    const shell = container.querySelector('[data-step="login"]') as HTMLElement;
    expect(shell.style.background).toContain('rgb(21, 16, 42)');
  });

  it('drops the old plum theme and title-case labels', () => {
    expect(src).not.toMatch(/#3a1a3a|#1a0a1e|#0a0410/);
    expect(src).not.toContain("'Sign In'");
    expect(src).not.toContain('Confirm Password');
  });

  it('one page: username and password together, one Sign in button, exact error', async () => {
    const { container } = render(<MemoryRouter><LoginPage /></MemoryRouter>);
    const user = container.querySelector('input[autocomplete="username"]') as HTMLInputElement;
    const pw = container.querySelector('input[type="password"]') as HTMLInputElement;
    expect(user.form).toBe(pw.form);
    expect(pw.getAttribute('autocomplete')).toBe('current-password');
    expect(screen.getByLabelText('Email or username')).toBe(user);
    expect(screen.getByLabelText('Password')).toBe(pw);
    expect(screen.queryByRole('button', { name: 'Continue' })).toBeNull();
    expect(screen.queryByText(/Welcome back/)).toBeNull();
    const submits = container.querySelectorAll('form button[type="submit"]');
    expect(submits.length).toBe(1);
    const btn = screen.getByRole('button', { name: 'Sign in' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.style.opacity).toBe('');
    expect(btn.style.color).toBe('rgb(199, 206, 219)');
    fireEvent.change(user, { target: { value: 'taylor' } });
    expect(btn.disabled).toBe(true);
    fireEvent.change(pw, { target: { value: ' secret ' } });
    expect(btn.disabled).toBe(false);
    await act(async () => { fireEvent.submit(user.form!); });
    expect(login).toHaveBeenCalledWith('taylor', ' secret ');
    expect(await screen.findByText('Invalid email, username, or password')).toBeTruthy();
    expect(user.value).toBe('taylor');
  });
});
