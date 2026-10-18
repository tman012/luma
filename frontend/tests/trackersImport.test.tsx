/** v170: Settings > Trackers > Import from Prowlarr (owner-triggered, preview first,
 * imported trackers off with Test required). A fake API only. */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TrackersSettings } from '@/components/trackers/TrackersSettings';
import { secondaryLabel } from '@/components/trackers/trackersApi';
import { readFileSync } from 'node:fs';
import TYPES from './fixtures/trackerTypes.json';

type Rec = Record<string, unknown> & { id: number; name: string; enabled: boolean };
let db: Rec[] = [];
let calls: { url: string; method: string; body: Record<string, unknown> | null }[] = [];
let available = true;
let previewStatus = 200;
const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;

const PREVIEW = {
  preview_id: 'pv1',
  indexers: [
    { key: 5, name: 'BeyondHD', type: 'beyondhd', type_name: 'BeyondHD', state: 'ready', missing: [] },
    { key: 7, name: 'AvistaZ', type: 'avistaz', type_name: 'AvistaZ', state: 'needs_credentials', missing: ['Password'] },
    { key: 3, name: 'IPTorrents', type: 'iptorrents', type_name: 'IPTorrents', state: 'exists', missing: [] },
    { key: 9, name: 'Some public site', type: null, type_name: null, state: 'unsupported', missing: [] },
  ],
};
const imported = (id: number, name: string, type: string, missing: string[] = []): Rec => ({
  id, name, type, type_name: name, protocol: 'torrent', enabled: false, url: 'https://x.example', categories: [2000],
  priority: 25, status: { state: 'untested', message: '', checked_at: null }, test_required: true, imported: true, missing,
});

function server() {
  return vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    calls.push({ url, method, body });
    if (url.endsWith('/api/trackers/types')) return res({ types: TYPES });
    if (url.endsWith('/api/trackers') && method === 'GET') return res({ trackers: db });
    if (url.endsWith('/api/trackers/import/prowlarr') && method === 'GET') return res({ available });
    if (url.endsWith('/api/trackers/import/prowlarr/preview')) {
      return previewStatus === 200 ? res(PREVIEW) : res({ error: "Couldn't reach Prowlarr" }, previewStatus);
    }
    if (url.endsWith('/api/trackers/import/prowlarr') && method === 'POST') {
      const keys = body!.keys as number[];
      const created = [];
      if (keys.includes(5)) created.push(imported(201, 'BeyondHD', 'beyondhd'));
      if (keys.includes(7)) created.push(imported(202, 'AvistaZ', 'avistaz', ['password']));
      db = [...db, ...created];
      return res({ created, skipped: [] }, 201);
    }
    const m = url.match(/\/api\/trackers\/(\d+)\/enabled$/);
    if (m) {
      const r = db.find(x => x.id === Number(m[1]))!;
      if (r.test_required && body!.enabled) return res({ error: 'Test required', fields: { enabled: 'Test required' } }, 409);
      r.enabled = Boolean(body!.enabled);
      return res({ tracker: r });
    }
    return res({ error: 'Not found' }, 404);
  });
}

function renderAt(wide = true) {
  return render(<MemoryRouter initialEntries={['/settings/trackers']}><Routes>
    <Route path="/settings/trackers/*" element={<TrackersSettings wide={wide} />} />
  </Routes></MemoryRouter>);
}

beforeEach(() => { db = []; calls = []; available = true; previewStatus = 200; vi.stubGlobal('fetch', server()); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Import from Prowlarr', () => {
  it('is offered only when Prowlarr is set up, and nothing is read until the owner asks', async () => {
    available = false;
    renderAt();
    await screen.findByTestId('trackers-empty');
    expect(screen.queryByTestId('tracker-import-open')).toBeNull();
    cleanup();
    available = true;
    renderAt();
    const open = await screen.findByTestId('tracker-import-open');
    expect(open.textContent).toBe('Import from Prowlarr');
    expect(calls.some(c => c.url.endsWith('/preview'))).toBe(false);
    expect(calls.some(c => c.method === 'POST')).toBe(false);
  });

  it('shows a preview first: ready rows ticked, chips in sentence case, no helper text', async () => {
    renderAt();
    fireEvent.click(await screen.findByTestId('tracker-import-open'));
    const sheet = await screen.findByTestId('tracker-import');
    const rows = await within(sheet).findAllByTestId('tracker-import-row');
    expect(rows.map(r => r.getAttribute('aria-checked'))).toEqual(['true', 'true', 'false', 'false']);
    expect((rows[2] as HTMLButtonElement).disabled).toBe(true);
    expect((rows[3] as HTMLButtonElement).disabled).toBe(true);
    const chips = within(sheet).getAllByTestId('tracker-import-chip').map(c => c.textContent);
    expect(chips).toEqual(['Enter credentials', 'Already added', 'Not supported yet']);
    expect(within(sheet).getByRole('heading').textContent).toBe('Import from Prowlarr');
    expect(within(sheet).getByTestId('tracker-import-go').textContent).toBe('Import 2');
    expect(sheet.querySelectorAll('p').length).toBe(0);              // no helper text
    for (const c of chips) expect(c![0]).toBe(c![0]!.toUpperCase());
    expect(calls.filter(c => c.method === 'POST' && c.url.endsWith('/import/prowlarr'))).toHaveLength(0);
  });

  it('imports only the ticked rows; they arrive off with Test required and a locked switch', async () => {
    renderAt();
    fireEvent.click(await screen.findByTestId('tracker-import-open'));
    const sheet = await screen.findByTestId('tracker-import');
    const rows = await within(sheet).findAllByTestId('tracker-import-row');
    fireEvent.click(rows[1]!);                                         // untick AvistaZ
    expect(within(sheet).getByTestId('tracker-import-go').textContent).toBe('Import 1');
    fireEvent.click(within(sheet).getByTestId('tracker-import-go'));
    await waitFor(() => expect(screen.queryByTestId('tracker-import')).toBeNull());
    const post = calls.find(c => c.method === 'POST' && c.url.endsWith('/import/prowlarr'))!;
    expect(post.body).toEqual({ preview_id: 'pv1', keys: [5] });
    const row = await screen.findByTestId('tracker-row');
    expect(row.getAttribute('data-enabled')).toBe('false');
    expect(within(row).getByTestId('tracker-test-required').textContent).toBe('Test required');
    const sw = within(row).getByTestId('tracker-switch');
    expect(sw.getAttribute('aria-disabled')).toBe('true');
    fireEvent.click(sw);
    expect(calls.some(c => c.url.includes('/enabled'))).toBe(false);
    expect(await screen.findByText('Added 1 tracker')).toBeTruthy();
  });

  it('a tracker missing credentials says so', async () => {
    db = [imported(202, 'AvistaZ', 'avistaz', ['password'])];
    renderAt();
    const row = await screen.findByTestId('tracker-row');
    expect(within(row).getByTestId('tracker-test-required').textContent).toBe('Enter credentials');
  });

  it('a Prowlarr that cannot be reached shows the error and Retry', async () => {
    previewStatus = 502;
    renderAt();
    fireEvent.click(await screen.findByTestId('tracker-import-open'));
    expect((await screen.findByTestId('tracker-import-error')).textContent).toBe("Couldn't reach Prowlarr");
    expect((screen.getByTestId("tracker-import-go") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Retry')).toBeTruthy();
  });

  it('phone: the import button sits under the list', async () => {
    renderAt(false);
    const open = await screen.findByTestId('tracker-import-open');
    expect(open.style.width).toBe('100%');
  });

  it('copy is American English and sentence case', () => {
    const src = readFileSync('src/components/trackers/ProwlarrImport.tsx', 'utf8');
    expect(src).not.toMatch(/Import From|Already Added|Not Supported|Enter Credentials|Test Required/);
    expect(src).not.toMatch(/colour|authoris|organis|licence/i);
  });
});


describe('v171 (Designer): secondary label and chips', () => {
  it('the type is hidden when it equals the name, in the modal and the list', async () => {
    expect(secondaryLabel('BeyondHD', 'BeyondHD')).toBeNull();
    expect(secondaryLabel('beyondhd ', 'BeyondHD')).toBeNull();
    expect(secondaryLabel('My IPT', 'IPTorrents')).toBe('IPTorrents');
    expect(secondaryLabel('X', null)).toBeNull();
    db = [imported(301, 'BeyondHD', 'beyondhd'), { ...imported(302, 'My IPT', 'iptorrents'), type_name: 'IPTorrents' }];
    renderAt();
    await screen.findByText('My IPT');
    expect(screen.getAllByTestId('tracker-type').map(t => t.textContent)).toEqual(['IPTorrents']);
    expect(document.body.textContent).not.toMatch(/BeyondHD\s*BeyondHD/);
    fireEvent.click(await screen.findByTestId('tracker-import-open'));
    const sheet = await screen.findByTestId('tracker-import');
    await within(sheet).findAllByTestId('tracker-import-row');
    expect(within(sheet).queryAllByTestId('import-type')).toHaveLength(0);
    const rows = within(sheet).getAllByTestId('tracker-import-row');
    rows.forEach((r, i) => expect(r.textContent!.split(PREVIEW.indexers[i].name).length - 1).toBe(1));   // the name once
  });

  it("the modal's status chips are 12px", async () => {
    renderAt();
    fireEvent.click(await screen.findByTestId('tracker-import-open'));
    const sheet = await screen.findByTestId('tracker-import');
    const chips = await within(sheet).findAllByTestId('tracker-import-chip');
    expect(chips.map(c => c.textContent)).toEqual(['Enter credentials', 'Already added', 'Not supported yet']);
    for (const c of chips) expect(c.style.fontSize).toBe('12px');
  });
});
