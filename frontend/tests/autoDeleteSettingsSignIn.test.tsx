/** PM ruling: turning on or widening automatic deletion (Downloads Guard)
 * needs the owner signed in with a session. On a 401, Save becomes a sign-in. */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsProvider } from '@/pages/settings/SettingsContext';
import { SettingsCard, SIGN_IN_TO_SAVE } from '@/pages/settings/components/SettingsCard';
import { ToggleSwitch } from '@/pages/settings/components/ToggleSwitch';
import { saveSettings } from '@/services/settingsApi';

const PATH = 'automation.downloads_guard.enabled';
const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let putStatus = 200;
const puts: unknown[] = [];
let saved: Record<string, unknown> = {};

function Where() {
  const loc = useLocation();
  return <div data-testid="where">{loc.pathname}|{JSON.stringify(loc.state)}</div>;
}

function renderCard() {
  return render(
    <MemoryRouter initialEntries={['/settings/automation']}>
      <Routes>
        <Route path="/settings/automation" element={
          <SettingsProvider>
            <SettingsCard title="Downloads Guard" paths={[PATH]}>
              <ToggleSwitch label="Enable Downloads Guard" path={PATH} />
            </SettingsCard>
          </SettingsProvider>
        } />
        <Route path="/login" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  putStatus = 200; puts.length = 0;
  saved = { automation: { downloads_guard: { enabled: false } } };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.endsWith('/api/settings/schema')) return res({});
    if (u.endsWith('/api/settings') && init?.method === 'PUT') {
      puts.push(JSON.parse(String(init.body)));
      if (putStatus === 401) return res({ error: 'Sign in first', sign_in: true }, 401);
      if (putStatus === 403) return res({ error: 'Owner only' }, 403);
      saved = JSON.parse(String(init.body));
      return res({ success: true, settings: saved });
    }
    if (u.endsWith('/api/settings')) return res(saved);
    return res({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function toggleAndSave() {
  renderCard();
  const sw = await screen.findByRole('switch');
  await act(async () => { fireEvent.click(sw); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Save$/ })); });
}

describe('Saving a change that widens automatic deletion', () => {
  it('a 401 turns Save into the sign-in prompt, which opens /login', async () => {
    putStatus = 401;
    await toggleAndSave();
    const btn = await screen.findByTestId('settings-sign-in-to-save');
    expect(btn.textContent).toBe(SIGN_IN_TO_SAVE);
    expect(SIGN_IN_TO_SAVE).toBe('Sign in to save');
    expect(screen.queryByRole('button', { name: /^Save$/ })).toBeNull();
    expect(screen.queryByText(/Save failed|401|API error/)).toBeNull();
    expect(puts).toEqual([{ automation: { downloads_guard: { enabled: true } } }]);
    await act(async () => { fireEvent.click(btn); });
    expect(screen.getByTestId('where').textContent).toBe('/login|{"from":"/settings/automation","signIn":true}');
  });

  it('undoing the edit brings Save back (a new change asks the server again)', async () => {
    putStatus = 401;
    await toggleAndSave();
    await screen.findByTestId('settings-sign-in-to-save');
    await act(async () => { fireEvent.click(screen.getByRole('switch')); });
    expect(screen.queryByTestId('settings-sign-in-to-save')).toBeNull();
  });

  it('the signed-in owner just saves', async () => {
    await toggleAndSave();
    await waitFor(() => expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('true'));
    expect(screen.queryByTestId('settings-sign-in-to-save')).toBeNull();
    expect(puts.length).toBe(1);
  });
});

describe('saveSettings', () => {
  it('maps 401 to signIn and 403 to plain words', async () => {
    putStatus = 401;
    expect(await saveSettings({ a: 1 })).toEqual({ success: false, signIn: true });
    putStatus = 403;
    expect(await saveSettings({ a: 1 })).toEqual({ success: false, errors: ['Only the owner can change this'] });
  });
});
