/** PM ruling (prod blocker): a real Downloads Guard run deletes files, so it
 * needs the owner signed in with a session. A dry run keeps today's access. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { triggerDownloadsGuardRun } from '@/services/settingsApi';

const res = (body: unknown, status = 200) => ({ ok: status < 300, status, statusText: '', json: async () => body }) as Response;
afterEach(() => vi.unstubAllGlobals());

describe('Downloads Guard: Clean up now', () => {
  it('a 401 on a real run reads as sign in', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res({ error: 'Sign in first', sign_in: true }, 401)));
    expect(await triggerDownloadsGuardRun(false)).toEqual({ ok: false, dry_run: false, signIn: true });
  });

  it('a 403 is an error, not a sign-in', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res({ error: 'Owner only' }, 403)));
    await expect(triggerDownloadsGuardRun(false)).rejects.toThrow('Owner only');
  });

  it('a signed-in owner runs as before, and the body says dry_run false', async () => {
    const f = vi.fn(async () => res({ ok: true, dry_run: false }));
    vi.stubGlobal('fetch', f);
    expect(await triggerDownloadsGuardRun(false)).toEqual({ ok: true, dry_run: false });
    const init = (f.mock.calls[0] as unknown[])[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ dry_run: false });
  });

  it('the card swaps Clean up for sign-in when implicit or after a 401', () => {
    const src = readFileSync(resolve(__dirname, '../src/pages/settings/DownloadsGuardCard.tsx'), 'utf8');
    expect(src).toContain("export const SIGN_IN_TO_CLEAN = 'Sign in to clean up';");
    expect(src).toContain('const showSignIn = Boolean(implicit) || needSignIn;');
    expect(src).toContain("if (r.signIn) { setNeedSignIn(true); return ''; }");
    expect(src).toMatch(/showSignIn \? \(\s*<button type="button" onClick=\{signIn\} data-testid="guard-sign-in"/);
    expect(src).toContain("navigate('/login', { state: { from: location.pathname + location.search, signIn: true } })");
  });
});
