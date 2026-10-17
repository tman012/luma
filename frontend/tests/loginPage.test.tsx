import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ login: vi.fn(), authenticated: false, loading: false, needsStart: false, pinUnlock: false, signupOpen: false }),
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
    const btn = screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement;
    expect(btn.style.background).toMatch(/rgb\(59, 130, 246\)|#3b82f6/);
    const shell = container.querySelector('[data-step="login"]') as HTMLElement;
    expect(shell.style.background).toContain('rgb(21, 16, 42)');
  });

  it('drops the old plum theme and title-case labels', () => {
    expect(src).not.toMatch(/#3a1a3a|#1a0a1e|#0a0410/);
    expect(src).not.toContain("'Sign In'");
    expect(src).not.toContain('Confirm Password');
  });
});
