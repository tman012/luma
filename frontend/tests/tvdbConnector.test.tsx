import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectorsSettings } from '@/components/home/ConnectorsSettings';
// v248: OMDb row (mocked backend; the real Test looks up tt0162222 server-side)

const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
let calls: { url: string; method: string; body: Record<string, unknown> | null }[] = [];
let view: Record<string, unknown>;

beforeEach(() => {
  calls = [];
  view = { tmdb: { set: true, last4: 'abcd' }, trakt: { set: false, last4: '' }, tvdb: { set: false, last4: '', pin_set: false } };
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('min-width: 1024px'), media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }));
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input); const method = (init?.method || 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    calls.push({ url, method, body });
    if (url.endsWith('/connectors/omdb/test')) return res(body?.api_key === 'bad' ? { ok: false, error: 'Invalid key' } : { ok: true, message: 'Connected' });
    if (url.endsWith('/connectors/omdb') && method === 'PUT') { view = { ...view, omdb: { set: !!body?.api_key, last4: body?.api_key ? 'om99' : '' } }; return res(view); }
    if (url.endsWith('/connectors/tvdb/test')) return res(body?.api_key === 'bad' ? { ok: false, error: 'Invalid key or PIN' } : { ok: true, message: 'Connected' });
    if (url.endsWith('/connectors/tvdb') && method === 'PUT') {
      view = { ...view, tvdb: { set: true, last4: 'wxyz', pin_set: !!body?.pin } };
      return res(view);
    }
    if (url.endsWith('/api/settings/connectors')) return res(view);
    return res({}, 404);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('TheTVDB connector', () => {
  it('a masked key and optional PIN; Test reports success or a short error; Save sends both', async () => {
    render(<ConnectorsSettings />);
    const key = await screen.findByTestId('tvdb-key') as HTMLInputElement;
    const pin = screen.getByTestId('tvdb-pin') as HTMLInputElement;
    expect([key.type, pin.type]).toEqual(['password', 'password']);
    expect(screen.getByText('Subscriber PIN (optional)')).toBeTruthy();
    fireEvent.change(key, { target: { value: 'bad' } });
    fireEvent.click(screen.getByTestId('tvdb-test'));
    await waitFor(() => expect(screen.getByTestId('tvdb-result').textContent).toBe('Invalid key or PIN'));
    fireEvent.change(key, { target: { value: 'good-key-wxyz' } });
    fireEvent.change(pin, { target: { value: '1234' } });
    fireEvent.click(screen.getByTestId('tvdb-test'));
    await waitFor(() => expect(screen.getByTestId('tvdb-result').textContent).toBe('Connected'));
    fireEvent.click(screen.getByTestId('tvdb-save'));
    await waitFor(() => expect(calls.some(c => c.method === 'PUT')).toBe(true));
    expect(calls.find(c => c.method === 'PUT')!.body).toEqual({ api_key: 'good-key-wxyz', pin: '1234' });
    await waitFor(() => expect(key.placeholder).toBe('••••wxyz'));
    expect(pin.placeholder).toBe('••••••••');
  });
});

describe('v248 OMDb connector', () => {
  it('a row right after TMDB, styled like the others: masked key, Test shows success or failure, Save', async () => {
    render(<ConnectorsSettings />);
    const key = await screen.findByTestId('omdb-key') as HTMLInputElement;
    expect(key.type).toBe('password');
    const rows = [...screen.getByTestId('connectors-section').querySelectorAll('[data-testid^="connector-"]')].map(e => e.getAttribute('data-testid'));
    expect(rows.slice(0, 2)).toEqual(['connector-tmdb', 'connector-omdb']);
    expect(screen.getByTestId('connector-omdb').querySelector('h2')!.textContent).toBe('OMDb');
    expect((screen.getByTestId('omdb-test') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(key, { target: { value: 'bad' } });
    fireEvent.click(screen.getByTestId('omdb-test'));
    await waitFor(() => expect(screen.getByTestId('omdb-result').textContent).toBe('Invalid key'));
    fireEvent.change(key, { target: { value: 'good-om99' } });
    fireEvent.click(screen.getByTestId('omdb-test'));
    await waitFor(() => expect(screen.getByTestId('omdb-result').textContent).toBe('Connected'));
    fireEvent.click(screen.getByTestId('omdb-save'));
    await waitFor(() => expect(key.placeholder).toBe('••••om99'));
    expect(calls.find(c => c.url.endsWith('/connectors/omdb') && c.method === 'PUT')!.body).toEqual({ api_key: 'good-om99' });
  });
});
