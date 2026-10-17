import { describe, expect, it } from 'vitest';
import { nextFromQuery } from '@/pages/LoginPage';

describe('nextFromQuery (server /login → SPA sign-in keeps next=)', () => {
  it('keeps same-site paths', () => {
    expect(nextFromQuery('?next=/settings/libraries')).toBe('/settings/libraries');
  });
  it('rejects outside or odd targets', () => {
    for (const q of ['?next=https://evil.example', '?next=//evil.example', '?next=/\\\\evil', '', '?next=']) {
      expect(nextFromQuery(q)).toBeNull();
    }
  });
  it('never loops back to /login', () => {
    expect(nextFromQuery('?next=/login')).toBe('/');
  });
});
