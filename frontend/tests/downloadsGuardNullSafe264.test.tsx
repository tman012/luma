import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DownloadsGuardSettings } from '@/components/automation/DownloadsGuardSettings';
import { DG_FALLBACK, normalizeDgStatus, normalizeDgView } from '@/components/automation/downloadsGuardApi';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const SETTINGS = { ...DG_FALLBACK, min_free_gb: 200, trackers: ['ipt'], protected_tags: ['luma-keep'] };
const VIEW = { settings: SETTINGS, defaults: DG_FALLBACK, folder: '/downloads', media_folder: '/downloads', trackers: [{ key: 'ipt', label: 'IPTorrents' }], allowed: true };

function stub(view: unknown, status: unknown, put?: unknown) {
  const errors: unknown[] = [];
  vi.spyOn(console, 'error').mockImplementation((...a: unknown[]) => { errors.push(a); });
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') return json(put ?? {});
    if (url.endsWith('/status')) return json(status);
    if (url.endsWith('/trackers')) return json({});
    return json(view);
  }));
  return errors;
}
const mount = () => render(<MemoryRouter><DownloadsGuardSettings /></MemoryRouter>);
const crashed = (errors: unknown[]) => errors.some(e => /min_free_gb|Cannot read properties/.test(String(e)));

describe('v264 Downloads Guard card is null-safe', () => {
  it('normalizeDgView: {} with nothing known is a failed load; missing fields fall back', () => {
    expect(normalizeDgView({})).toBeNull();
    expect(normalizeDgView(null)).toBeNull();
    const v = normalizeDgView({ defaults: DG_FALLBACK })!;
    expect(v.settings.min_free_gb).toBe(150);
    expect(v.trackers).toEqual([]);
    expect(v.allowed).toBe(false);
    const part = normalizeDgView({ settings: { min_free_gb: 300 } })!;
    expect(part.settings).toEqual({ ...DG_FALLBACK, min_free_gb: 300 });
    expect(normalizeDgView({ settings: { trackers: null, protected_tags: 'x' } })!.settings.protected_tags).toEqual([]);
  });

  it('normalizeDgView keeps the last known view for an answer of {}', () => {
    const prev = normalizeDgView(VIEW)!;
    expect(normalizeDgView({}, prev)).toEqual(prev);
  });

  it('normalizeDgStatus: {} is no status; a last result always has lists', () => {
    expect(normalizeDgStatus({})).toEqual({ status: null, log: [] });
    const n = normalizeDgStatus({ status: { last_result: { dry_run: true } } });
    expect(n.status!.last_result!.deleted).toEqual([]);
    expect(n.status!.last_result!.failed).toEqual([]);
    expect(n.status!.running).toBe(false);
  });

  it('a settings answer of {} shows Couldn\'t load instead of throwing', async () => {
    const errors = stub({}, {});
    mount();
    expect(await screen.findByText("Couldn't load")).toBeTruthy();
    expect(crashed(errors)).toBe(false);
  });

  it('a tracker summary of {} does not throw', async () => {
    const errors = stub(VIEW, {});
    mount();
    expect(await screen.findByText('Free-space floor (GB)')).toBeTruthy();
    expect(errors.some(e => /Cannot read properties/.test(String(e)))).toBe(false);
  });

  it('settings without fields and a status of {} still render the card', async () => {
    const errors = stub({ settings: {} }, {});
    mount();
    expect(await screen.findByText('Free-space floor (GB)')).toBeTruthy();
    expect(crashed(errors)).toBe(false);
  });

  it('a save answered with {} keeps the change and does not throw', async () => {
    const errors = stub(VIEW, { status: {}, log: null }, {});
    const { container } = mount();
    await screen.findByText('Free-space floor (GB)');
    const floor = container.querySelector('#dg-floor') as HTMLInputElement;
    fireEvent.change(floor, { target: { value: '250' } });
    fireEvent.blur(floor);
    await waitFor(() => expect((container.querySelector('#dg-floor') as HTMLInputElement).value).toBe('250'));
    expect(crashed(errors)).toBe(false);
  });
});
