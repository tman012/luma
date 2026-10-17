import { readFileSync } from 'fs';
import { resolve } from 'path';
import { describe, expect, it } from 'vitest';

const src = readFileSync(resolve(__dirname, '../src/pages/LoginPage.tsx'), 'utf8');

describe('login sign-in field', () => {
  it('accepts an email or a username', () => {
    const signin = src.split("signinStep === 'email' && (")[1]!.split('</form>')[0]!;
    expect(signin).toContain('Email or username');
    expect(signin).toContain('type="text"');
    expect(signin).toContain('autoComplete="username"');
    expect(signin).not.toContain('type="email"');
  });
});
