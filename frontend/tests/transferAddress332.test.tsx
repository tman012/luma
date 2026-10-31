import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const auth = { authenticated: true, loading: false, implicit: false, logout: vi.fn(),
  user: { id: 1, name: 'owner', is_admin: true } };
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));

import { LibraryPathsSettings } from '@/components/library/LibraryPathsSettings';
import type { LibraryView } from '@/components/library/libraryPathsApi';

const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
type Call = { url: string; method: string; body: Record<string, unknown> | null };
let calls: Call[] = [];
let state: LibraryView;
let reject: string | null = null;

const base = (): LibraryView => ({ paths: [], rules: [], fallback_path_id: null, path_map: [], transfer: 'copy' });

beforeEach(() => {
  state = base(); calls = []; reject = null;
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: false, media: q, addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }));
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    calls.push({ url, method, body });
    if (url.endsWith('/api/settings/library-paths/mapping')) {
      if (reject) return res({ errors: { transfer_address: reject } }, 400);
      state = { ...state, ...(body as Partial<LibraryView>) };
      return res(state);
    }
    if (url.endsWith('/api/settings/library-paths')) return res(state);
    return res({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const puts = () => calls.filter(c => c.url.endsWith('/mapping'));

describe('Transfer address field (v332)', () => {
  it('a missing key shows an empty field with a label only and Save disabled', async () => {
    render(<LibraryPathsSettings />);
    const input = await screen.findByTestId('lp-transfer-address') as HTMLInputElement;
    expect(input.value).toBe('');
    expect(screen.getByLabelText('Transfer address')).toBe(input);
    expect(input.placeholder).toBe('');
    expect(screen.getByTestId('lp-transfer-address-form').textContent).toBe('Transfer addressSave');
    expect((screen.getByTestId('lp-transfer-address-save') as HTMLButtonElement).disabled).toBe(true);
  });

  it('saves the trimmed address through the mapping API and shows the saved value', async () => {
    render(<LibraryPathsSettings />);
    const input = await screen.findByTestId('lp-transfer-address') as HTMLInputElement;
    fireEvent.change(input, { target: { value: ' 10.0.0.5 ' } });
    fireEvent.click(screen.getByTestId('lp-transfer-address-save'));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]!.method).toBe('PUT');
    expect(puts()[0]!.body).toEqual({ transfer_address: '10.0.0.5' });
    await waitFor(() => expect(input.value).toBe('10.0.0.5'));
    expect((screen.getByTestId('lp-transfer-address-save') as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the server error under the field and keeps the typed value', async () => {
    reject = 'Enter an IP address or host name';
    render(<LibraryPathsSettings />);
    const input = await screen.findByTestId('lp-transfer-address') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'smb://nas' } });
    fireEvent.submit(screen.getByTestId('lp-transfer-address-form'));
    expect((await screen.findByTestId('lp-transfer-address-error')).textContent).toBe('Enter an IP address or host name');
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(input.value).toBe('smb://nas');
  });

  it('clearing a saved address sends blank', async () => {
    state = { ...base(), transfer_address: '10.0.0.5' };
    render(<LibraryPathsSettings />);
    const input = await screen.findByTestId('lp-transfer-address') as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe('10.0.0.5'));
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByTestId('lp-transfer-address-save'));
    await waitFor(() => expect(puts()[0]?.body).toEqual({ transfer_address: '' }));
  });
});
