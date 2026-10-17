import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TrackersSettings, useTrackersAccess } from '@/components/trackers/TrackersSettings';
import TYPES from './fixtures/trackerTypes.json';

const MASK = '********';
type Rec = Record<string, unknown> & { id: number; name: string; enabled: boolean };
let db: Rec[] = [];
let calls: { url: string; method: string; body: Record<string, unknown> | null }[] = [];
let denied: 0 | 401 | 403 = 0;
let override: ((url: string, method: string, body: Record<string, unknown> | null) => Response | Promise<Response> | null) | null = null;

const res = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
const typeName = (id: string) => (TYPES as { id: string; name: string }[]).find(t => t.id === id)!.name;
function rec(over: Partial<Rec> & { id: number; name: string; type: string }): Rec {
  return { type_name: typeName(over.type), protocol: 'torrent', enabled: true, url: 'https://idx.example', categories: [2000, 5000],
    priority: 25, status: { state: 'untested', message: '', checked_at: null }, ...over } as Rec;
}

function server() {
  return vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    calls.push({ url, method, body });
    if (denied) return res({ error: 'Owner access required' }, denied);
    const o = override && await override(url, method, body);
    if (o) return o;
    if (url.endsWith('/api/trackers/types')) return res({ types: TYPES });
    if (url.endsWith('/api/trackers') && method === 'GET') return res({ trackers: db });
    if (url.endsWith('/api/trackers') && method === 'POST') {
      const r = rec({ ...(body as Rec), id: 100 + db.length, type: String(body!.type) });
      db = [...db, r];
      return res({ tracker: r }, 201);
    }
    let m = url.match(/\/api\/trackers\/(\d+)\/enabled$/);
    if (m) {
      const r = db.find(x => x.id === Number(m![1]));
      if (!r) return res({ error: 'Not found' }, 404);
      r.enabled = Boolean(body!.enabled);
      return res({ tracker: r });
    }
    m = url.match(/\/api\/trackers\/(\d+)\/test$/);
    if (m) return res({ ok: true, message: 'Connected' });
    if (url.endsWith('/api/trackers/test')) return res({ ok: true, message: 'Connected' });
    m = url.match(/\/api\/trackers\/(\d+)$/);
    if (m) {
      const id = Number(m[1]);
      if (method === 'DELETE') { db = db.filter(x => x.id !== id); return res({ ok: true }); }
      if (method === 'PUT') {
        const i = db.findIndex(x => x.id === id);
        db[i] = { ...db[i]!, ...body };
        return res({ tracker: db[i] });
      }
    }
    return res({ error: 'Not found' }, 404);
  });
}

function Where() { const l = useLocation(); return <p data-testid="where">{l.pathname + l.search}</p>; }
function renderAt(path: string, wide = true) {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/settings/trackers/*" element={<TrackersSettings wide={wide} />} />
    <Route path="/settings" element={<p>settings list</p>} />
  </Routes><Where /></MemoryRouter>);
}
const where = () => screen.getByTestId('where').textContent;
const lastCall = (pred: (c: (typeof calls)[number]) => boolean) => [...calls].reverse().find(pred);

beforeEach(() => {
  db = []; calls = []; denied = 0; override = null;
  vi.stubGlobal('fetch', server());
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('Trackers list', () => {
  it('desktop empty: title, Add tracker and one "No trackers" row, nothing else', async () => {
    renderAt('/settings/trackers');
    const empty = await screen.findByTestId('trackers-empty');
    expect(empty.textContent).toBe('No trackers');
    expect(screen.getByRole('heading', { level: 2, name: 'Trackers' })).toBeTruthy();
    const add = screen.getByRole('button', { name: 'Add tracker' });
    expect(add.style.height).toBe('40px');
    expect(add.style.background).toBe('var(--color-accent)');
    expect(screen.getByTestId('trackers-section').textContent).toBe('TrackersAdd trackerNo trackers');
    const card = screen.getByTestId('trackers-card');
    expect(card.style.borderRadius).toBe('16px');
  });

  it('phone empty: circular back, centered title, circular Plus', async () => {
    renderAt('/settings/trackers', false);
    await screen.findByTestId('trackers-empty');
    const header = screen.getByTestId('trackers-phone-header');
    expect(within(header).getByRole('heading', { name: 'Trackers' })).toBeTruthy();
    fireEvent.click(within(header).getByRole('button', { name: 'Add tracker' }));
    await waitFor(() => expect(where()).toBe('/settings/trackers/new'));
    expect(screen.getByTestId('tracker-type-picker')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();   // a pushed page, not a modal
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    await waitFor(() => expect(where()).toBe('/settings'));
  });

  it('rows: dot by status, type name, disabled rows dimmed, server order kept', async () => {
    db = [
      rec({ id: 1, name: 'Alpha', type: 'torznab', status: { state: 'ok', message: '', checked_at: 1 } }),
      rec({ id: 2, name: 'Bravo', type: 'iptorrents', status: { state: 'error', message: 'Unauthorized', checked_at: 1 } }),
      rec({ id: 3, name: 'Charlie', type: 'newznab', enabled: false, status: { state: 'ok', message: '', checked_at: 1 } }),
      rec({ id: 4, name: 'Delta', type: 'torznab' }),
    ];
    renderAt('/settings/trackers');
    const rows = await screen.findAllByTestId('tracker-row');
    expect(rows.map(r => within(r).getByTestId('tracker-name').textContent)).toEqual(['Alpha', 'Bravo', 'Charlie', 'Delta']);
    expect(rows.map(r => within(r).getByTestId('tracker-type').textContent)).toEqual(['Torznab', 'IPTorrents', 'Newznab', 'Torznab']);
    const dots = rows.map(r => within(r).getByTestId('tracker-dot'));
    expect(dots.map(d => d.style.background)).toEqual(['var(--color-success)', 'var(--color-danger)', 'rgba(255, 255, 255, 0.3)', 'rgba(255, 255, 255, 0.3)']);
    expect(within(rows[2]!).getByTestId('tracker-name').style.color).toBe('var(--color-text-muted)');
    expect(within(rows[0]!).getByTestId('tracker-name').style.color).toBe('var(--color-text-primary)');
    expect(rows[0]!.style.height).toBe('56px');
    // desktop switch 44x26
    const track = within(rows[0]!).getByRole('switch').querySelector('[data-track]') as HTMLElement;
    expect([track.style.width, track.style.height]).toEqual(['44px', '26px']);
  });

  it('phone switch is 51x31', async () => {
    db = [rec({ id: 1, name: 'Alpha', type: 'torznab' })];
    renderAt('/settings/trackers', false);
    const sw = await screen.findByRole('switch', { name: 'Alpha' });
    const track = sw.querySelector('[data-track]') as HTMLElement;
    expect([track.style.width, track.style.height]).toEqual(['51px', '31px']);
  });

  it('row click opens edit; the switch toggles enable without opening', async () => {
    db = [rec({ id: 7, name: 'Alpha', type: 'torznab' })];
    renderAt('/settings/trackers');
    const sw = await screen.findByRole('switch', { name: 'Alpha' });
    fireEvent.click(sw);
    await waitFor(() => expect(lastCall(c => c.url.endsWith('/7/enabled'))?.body).toEqual({ enabled: false }));
    expect(where()).toBe('/settings/trackers');
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Alpha' }).getAttribute('aria-checked')).toBe('false'));
    fireEvent.click(screen.getByText('Alpha'));
    await waitFor(() => expect(where()).toBe('/settings/trackers/7'));
    expect(await screen.findByTestId('tracker-form')).toBeTruthy();
  });

  it('a failed toggle reverts and says "Couldn\'t save"', async () => {
    db = [rec({ id: 7, name: 'Alpha', type: 'torznab' })];
    override = (url, method) => (method === 'PUT' && url.endsWith('/enabled') ? res({ error: 'boom' }, 500) : null);
    renderAt('/settings/trackers');
    fireEvent.click(await screen.findByRole('switch', { name: 'Alpha' }));
    await waitFor(() => expect(screen.getByTestId('trackers-toast').textContent).toBe("Couldn't save"));
    expect(screen.getByRole('switch', { name: 'Alpha' }).getAttribute('aria-checked')).toBe('true');
  });

  it('load failure: one line and Retry', async () => {
    let fail = true;
    override = url => (fail && url.endsWith('/api/trackers') ? res({}, 500) : null);
    renderAt('/settings/trackers');
    expect((await screen.findByTestId('trackers-load-error')).textContent).toBe("Couldn't load trackersRetry");
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('trackers-empty')).toBeTruthy();
  });
});

describe('Owner only', () => {
  it.each([401, 403] as const)('renders nothing on %i', async code => {
    denied = code;
    renderAt('/settings/trackers');
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    await act(async () => { await new Promise(r => setTimeout(r, 10)); });
    expect(screen.queryByTestId('trackers-section')).toBeNull();
  });

  it('useTrackersAccess: true for the owner, false otherwise, skipped when disabled', async () => {
    function Probe({ on }: { on: boolean }) { const a = useTrackersAccess(on); return <p data-testid="a">{String(a)}</p>; }
    const { unmount } = render(<Probe on />);
    await waitFor(() => expect(screen.getByTestId('a').textContent).toBe('true'));
    unmount();
    denied = 401;
    const r2 = render(<Probe on />);
    await waitFor(() => expect(screen.getByTestId('a').textContent).toBe('false'));
    r2.unmount();
    calls = [];
    render(<Probe on={false} />);
    expect(screen.getByTestId('a').textContent).toBe('false');
    expect(calls).toEqual([]);
  });
});

describe('Add tracker: type picker', () => {
  it('desktop modal: Torznab, Newznab, then sites A-Z; no Jackett or Prowlarr; search above 8', async () => {
    renderAt('/settings/trackers');
    fireEvent.click(await screen.findByRole('button', { name: 'Add tracker' }));
    const dlg = await screen.findByRole('dialog', { name: 'Add tracker' });
    expect(dlg.style.maxWidth).toBe('480px');
    expect(dlg.style.borderRadius).toBe('20px');
    const names = within(dlg).getAllByTestId('tracker-type').map(b => b.textContent);
    expect(names).toEqual(['Torznab', 'Newznab', 'AvistaZ', 'BeyondHD', 'CinemaZ', 'ExoticaZ', 'IPTorrents', 'MoreThanTV', 'PrivateHD']);
    expect(names.join(' ')).not.toMatch(/jackett|prowlarr/i);
    const search = within(dlg).getByRole('searchbox', { name: 'Search' }) as HTMLInputElement;
    expect(search.placeholder).toBe('Search');
    fireEvent.change(search, { target: { value: 'ipt' } });
    expect(within(dlg).getAllByTestId('tracker-type').map(b => b.textContent)).toEqual(['IPTorrents']);
    fireEvent.click(within(dlg).getByText('IPTorrents'));
    await waitFor(() => expect(where()).toBe('/settings/trackers/new?type=iptorrents'));
    expect(screen.getByRole('heading', { level: 2, name: 'IPTorrents' })).toBeTruthy();
  });

  it('Esc and the close button dismiss it', async () => {
    renderAt('/settings/trackers/new');
    await screen.findByRole('dialog', { name: 'Add tracker' });
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
    fireEvent.click(screen.getByRole('button', { name: 'Add tracker' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
  });

  it('an unknown type goes back to the picker', async () => {
    renderAt('/settings/trackers/new?type=jackett');
    await waitFor(() => expect(where()).toBe('/settings/trackers/new'));
  });
});

function fill(label: string, value: string) {
  const el = screen.getByLabelText(label) as HTMLInputElement;
  fireEvent.change(el, { target: { value } });
  fireEvent.blur(el);
  return el;
}

describe('Tracker form: add', () => {
  it('torznab: schema fields in order, Enabled on, Save disabled until valid', async () => {
    renderAt('/settings/trackers/new?type=torznab');
    const form = await screen.findByTestId('tracker-form');
    expect(screen.getByRole('heading', { level: 2, name: 'Torznab' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Trackers/ })).toBeTruthy();
    expect(screen.getByRole('switch', { name: 'Enabled' }).getAttribute('aria-checked')).toBe('true');
    const labels = [...form.querySelectorAll('label, [id$="-label"]')].map(l => l.textContent);
    expect(labels).toEqual(['Enabled', 'Name', 'URL', 'API key', 'Categories', 'Priority']);
    expect((screen.getByLabelText('Priority') as HTMLInputElement).value).toBe('25');
    expect(screen.getAllByTestId('tracker-category').filter(c => c.getAttribute('aria-pressed') === 'true').map(c => c.textContent)).toEqual(['Movies', 'TV']);
    const save = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
    fill('Name', 'My indexer');
    fill('URL', 'ftp://idx.example');
    expect(screen.getByText('Invalid URL')).toBeTruthy();
    expect(screen.getByLabelText('URL').getAttribute('aria-invalid')).toBe('true');
    expect(save.disabled).toBe(true);
    fill('URL', 'https://idx.example/api/');
    expect(screen.queryByText('Invalid URL')).toBeNull();
    expect(save.disabled).toBe(false);
    fill('Priority', '51');
    expect(screen.getByText('Invalid')).toBeTruthy();
    expect(save.disabled).toBe(true);
    fill('Priority', '10');
    fill('API key', 'k-123');
    fireEvent.click(screen.getByRole('button', { name: 'Movies' }));
    fireEvent.click(screen.getByRole('button', { name: 'TV' }));
    expect(screen.getByText('Required')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Movies/UHD' }));
    fireEvent.click(save);
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
    const post = lastCall(c => c.method === 'POST' && c.url.endsWith('/api/trackers'))!;
    expect(post.body).toEqual({ type: 'torznab', enabled: true, name: 'My indexer', url: 'https://idx.example/api',
      api_key: 'k-123', categories: [2045], priority: 10 });
    expect(screen.getByTestId('trackers-toast').textContent).toBe('Saved');
    expect(await screen.findByText('My indexer')).toBeTruthy();
  });

  it('site types need https; name starts as the type name', async () => {
    renderAt('/settings/trackers/new?type=iptorrents');
    await screen.findByTestId('tracker-form');
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('IPTorrents');
    expect((screen.getByLabelText('URL') as HTMLInputElement).value).toBe('https://iptorrents.com');
    fill('URL', 'http://iptorrents.com');
    expect(screen.getByText('Invalid URL')).toBeTruthy();
    fill('URL', 'https://iptorrents.com');
    const cookie = screen.getByLabelText('Cookie') as HTMLInputElement;
    expect(cookie.type).toBe('password');
    fireEvent.blur(cookie);
    expect(screen.getByText('Required')).toBeTruthy();
  });

  it('server field errors land under their field; 409 shows "Credentials unreadable"', async () => {
    let step = 0;
    override = (url, method) => {
      if (!(method === 'POST' && url.endsWith('/api/trackers'))) return null;
      step++;
      return step === 1 ? res({ error: 'Invalid', fields: { url: 'Invalid URL', name: 'Name in use' } }, 400)
        : res({ error: 'Credentials unreadable' }, 409);
    };
    renderAt('/settings/trackers/new?type=torznab');
    await screen.findByTestId('tracker-form');
    fill('Name', 'Dup');
    fill('URL', 'https://idx.example/1/api');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Invalid URL')).toBeTruthy();
    expect(screen.getByText('Name in use')).toBeTruthy();
    expect(where()).toBe('/settings/trackers/new?type=torznab');
    fill('URL', 'https://idx.example');
    fill('Name', 'Other');
    expect(screen.queryByText('Invalid URL')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect((await screen.findByTestId('tracker-form-error')).textContent).toBe('Credentials unreadable');
  });

  it.each([[403, 'Owner access required'], [429, 'Too many changes. Try again in a minute.']])(
    'Save shows the server message on %i', async (status, msg) => {
      override = (url, method) => (method === 'POST' && url.endsWith('/api/trackers') ? res({ error: msg }, status) : null);
      renderAt('/settings/trackers/new?type=torznab');
      await screen.findByTestId('tracker-form');
      fill('Name', 'X');
      fill('URL', 'https://idx.example');
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      expect((await screen.findByTestId('tracker-form-error')).textContent).toBe(msg);
    });

  it.each([429, 500, 503])('useTrackersAccess: a %i keeps Trackers visible; the section shows the error and Retry', async (st) => {
    function Probe() { const a = useTrackersAccess(true); return <p data-testid="a">{String(a)}</p>; }
    override = url => (url.endsWith('/api/trackers/types') ? res({ error: 'Busy' }, st) : null);
    const r1 = render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('a').textContent).toBe('true'));
    r1.unmount();
    render(<MemoryRouter initialEntries={['/settings/trackers']}><TrackersSettings wide /></MemoryRouter>);
    expect(await screen.findByText("Couldn't load trackers")).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('useTrackersAccess: 403 is final (one call, hidden)', async () => {
    function Probe() { const a = useTrackersAccess(true); return <p data-testid="a">{String(a)}</p>; }
    override = null; denied = 403;
    const before = calls.length;
    render(<Probe />);
    await waitFor(() => expect(screen.getByTestId('a').textContent).toBe('false'));
    expect(calls.length - before).toBe(1);
  });

  it('Test: spinner, then "Connected" for 4 s; the form body goes to /api/trackers/test', async () => {
    let release: (r: Response) => void = () => {};
    override = url => (url.endsWith('/api/trackers/test') ? new Promise<Response>(r => { release = r; }) : null);
    renderAt('/settings/trackers/new?type=torznab');
    await screen.findByTestId('tracker-form');
    fill('Name', 'X');
    fill('URL', 'https://idx.example');
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    await waitFor(() => expect(screen.getByTestId('tracker-spinner')).toBeTruthy());
    expect((screen.getByTestId('tracker-test') as HTMLButtonElement).disabled).toBe(true);
    const body = lastCall(c => c.url.endsWith('/api/trackers/test'))!.body;
    expect(body).toEqual({ type: 'torznab', enabled: true, name: 'X', url: 'https://idx.example', categories: [2000, 5000], priority: 25 });
    await act(async () => { release(res({ ok: true, message: 'Connected' })); });
    await waitFor(() => expect(screen.getByTestId('tracker-test-result').textContent).toBe('Connected'));
    expect(screen.queryByTestId('tracker-spinner')).toBeNull();
    expect(screen.queryByTestId('trackers-toast')).toBeNull();   // no toasts for Test
    await act(async () => { vi.advanceTimersByTime(4100); });
    expect(screen.getByTestId('tracker-test-result').textContent).toBe('');
  });

  it.each([['Address not allowed', 'Address not allowed'], ['Unauthorized', 'Unauthorized'], ['Timed out', 'Timed out'],
    ['Rate limited', 'Too many tests. Try again in a few minutes.']])('Test shows the server error "%s" in red', async (err, msg) => {
    override = url => (url.endsWith('/api/trackers/test') ? res({ ok: false, error: err }) : null);
    renderAt('/settings/trackers/new?type=torznab');
    await screen.findByTestId('tracker-form');
    fill('Name', 'X');
    fill('URL', 'https://idx.example');
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    const out = await screen.findByText(msg);
    expect(screen.getByTestId('tracker-test-result').style.color).toBe('var(--color-danger-text)');
    expect(out).toBeTruthy();
  });

  it('Test with an invalid form shows the field errors and calls nothing', async () => {
    renderAt('/settings/trackers/new?type=torznab');
    await screen.findByTestId('tracker-form');
    calls = [];
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    expect(screen.getAllByText('Required').length).toBe(2);   // Name, URL
    expect(calls).toEqual([]);
  });

  it('back with unsaved changes asks "Discard changes?"', async () => {
    renderAt('/settings/trackers/new?type=torznab');
    await screen.findByTestId('tracker-form');
    fill('Name', 'X');
    fireEvent.click(screen.getByTestId('tracker-back'));
    const dlg = await screen.findByRole('dialog', { name: 'Discard changes?' });
    expect(dlg.style.maxWidth).toBe('400px');
    const discard = within(dlg).getByRole('button', { name: 'Discard' });
    expect(discard.style.background).toBe('var(--color-danger)');
    fireEvent.click(within(dlg).getByRole('button', { name: 'Keep editing' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(where()).toBe('/settings/trackers/new?type=torznab');
    fireEvent.click(screen.getByTestId('tracker-back'));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
  });

  it('back without changes leaves at once', async () => {
    renderAt('/settings/trackers/new?type=torznab');
    await screen.findByTestId('tracker-form');
    fireEvent.click(screen.getByTestId('tracker-back'));
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
  });

  it('copy: only labels, values and actions, no helper text', async () => {
    renderAt('/settings/trackers/new?type=avistaz');
    const form = await screen.findByTestId('tracker-form');
    const texts = [...form.querySelectorAll('*')].flatMap(el => [...el.childNodes]
      .filter(n => n.nodeType === 3 && n.textContent!.trim()).map(n => n.textContent!.trim()));
    expect(texts.sort()).toEqual(['AvistaZ', 'Categories', 'Enabled', 'Movies', 'Name', 'PID', 'Password', 'Priority',
      'Save', 'TV', 'Test', 'Trackers', 'URL', 'Username'].sort());
    for (const input of form.querySelectorAll('input')) expect(input.placeholder).toBe('');
  });
});

describe('Tracker form: edit and secrets', () => {
  beforeEach(() => {
    db = [
      rec({ id: 5, name: 'IPT', type: 'iptorrents', url: 'https://iptorrents.com', cookie: MASK, user_agent: 'UA/1',
        categories: [2000], priority: 3 }),
      rec({ id: 6, name: 'Idx', type: 'torznab', url: 'https://idx.example', api_key: MASK }),
    ];
  });

  it('a stored secret is never put in the input; the mask is a placeholder only', async () => {
    renderAt('/settings/trackers/5');
    await screen.findByTestId('tracker-form');
    expect(screen.getByRole('heading', { level: 2, name: 'IPT' })).toBeTruthy();
    const cookie = screen.getByLabelText('Cookie') as HTMLInputElement;
    expect(cookie.value).toBe('');
    expect(cookie.type).toBe('password');
    expect(cookie.placeholder).toBe('••••••••');
    expect(cookie.autocomplete).toBe('new-password');
    for (const input of document.querySelectorAll('input')) expect((input as HTMLInputElement).value).not.toContain(MASK);
    expect(document.body.innerHTML).not.toContain(MASK);
    expect((screen.getByLabelText('User agent') as HTMLInputElement).value).toBe('UA/1');
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('eye shows only what was typed here', async () => {
    renderAt('/settings/trackers/5');
    await screen.findByTestId('tracker-form');
    const cookie = screen.getByLabelText('Cookie') as HTMLInputElement;
    fireEvent.click(screen.getByRole('button', { name: 'Show Cookie' }));
    expect(cookie.type).toBe('text');
    expect(cookie.value).toBe('');
    fireEvent.change(cookie, { target: { value: 'uid=1; pass=2' } });
    expect(cookie.value).toBe('uid=1; pass=2');
    fireEvent.click(screen.getByRole('button', { name: 'Hide Cookie' }));
    expect(cookie.type).toBe('password');
  });

  it('editing the name keeps the stored secret (left out of the PUT)', async () => {
    renderAt('/settings/trackers/5');
    await screen.findByTestId('tracker-form');
    fill('Name', 'IPT 2');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
    const put = lastCall(c => c.method === 'PUT' && c.url.endsWith('/api/trackers/5'))!;
    expect(put.body).toEqual({ type: 'iptorrents', enabled: true, name: 'IPT 2', url: 'https://iptorrents.com',
      user_agent: 'UA/1', categories: [2000], priority: 3 });
    expect('cookie' in put.body!).toBe(false);
    expect(screen.getByTestId('trackers-toast').textContent).toBe('Saved');
  });

  it('a new URL drops the stored secret: a required one must be entered again', async () => {
    renderAt('/settings/trackers/5');
    await screen.findByTestId('tracker-form');
    fill('URL', 'https://ipt.mirror.example');
    const cookie = screen.getByLabelText('Cookie') as HTMLInputElement;
    expect(cookie.placeholder).toBe('');
    fireEvent.blur(cookie);
    expect(screen.getByText('Required')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fill('Cookie', 'uid=9');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
    expect(lastCall(c => c.method === 'PUT')!.body!.cookie).toBe('uid=9');
  });

  it('an optional stored secret emptied on purpose is cleared with ""', async () => {
    renderAt('/settings/trackers/6');
    await screen.findByTestId('tracker-form');
    const key = screen.getByLabelText('API key') as HTMLInputElement;
    fireEvent.change(key, { target: { value: 'x' } });
    fireEvent.change(key, { target: { value: '' } });
    expect(key.placeholder).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
    expect(lastCall(c => c.method === 'PUT')!.body!.api_key).toBe('');
  });

  it('Test on an unchanged tracker tests the saved one; with edits it sends the form plus id', async () => {
    renderAt('/settings/trackers/6');
    await screen.findByTestId('tracker-form');
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    await screen.findByText('Connected');
    expect(lastCall(c => c.url.includes('/test'))!.url).toMatch(/\/api\/trackers\/6\/test$/);
    fill('Priority', '7');
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    const n = calls.filter(c => c.url.includes('/test')).length;
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    // A second Test within 3 s waits its turn instead of drawing "Rate limited".
    await new Promise(r => setTimeout(r, 300));
    expect(calls.filter(c => c.url.includes('/test')).length).toBe(n);
    await waitFor(() => expect(lastCall(c => c.url.includes('/test'))!.url).toMatch(/\/api\/trackers\/test$/), { timeout: 5000 });
    const body = lastCall(c => c.url.endsWith('/api/trackers/test'))!.body!;
    expect(body.id).toBe(6);
    expect(body.priority).toBe(7);
    expect('api_key' in body).toBe(false);
  }, 10000);

  it('Delete asks "Delete <name>?" and returns to the list', async () => {
    renderAt('/settings/trackers/6');
    await screen.findByTestId('tracker-form');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dlg = await screen.findByRole('dialog', { name: 'Delete Idx?' });
    expect(within(dlg).getByRole('button', { name: 'Delete' }).style.background).toBe('var(--color-danger)');
    fireEvent.click(within(dlg).getByRole('button', { name: 'Cancel' }));
    expect(lastCall(c => c.method === 'DELETE')).toBeUndefined();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
    expect(lastCall(c => c.method === 'DELETE')!.url).toMatch(/\/api\/trackers\/6$/);
    await waitFor(() => expect(screen.queryByText('Idx')).toBeNull());
    expect(screen.getByText('IPT')).toBeTruthy();
  });

  it('unreadable stored credentials: the secret shows the error and must be entered again', async () => {
    db[0] = { ...db[0]!, status: { state: 'error', message: 'Credentials unreadable', checked_at: 1 } };
    renderAt('/settings/trackers/5');
    await screen.findByTestId('tracker-form');
    expect(screen.getByText('Credentials unreadable')).toBeTruthy();
    expect((screen.getByLabelText('Cookie') as HTMLInputElement).placeholder).toBe('');
    fill('Name', 'IPT 3');
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('an unknown id goes back to the list', async () => {
    renderAt('/settings/trackers/999');
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
  });

  it('phone: pushed page, Save first, then Test, Delete as a red row at the bottom', async () => {
    renderAt('/settings/trackers/6', false);
    const form = await screen.findByTestId('tracker-form');
    expect(within(screen.getByTestId('trackers-phone-header')).getByRole('heading', { name: 'Idx' })).toBeTruthy();
    const order = [...form.querySelectorAll('button')].map(b => b.textContent).filter(t => ['Save', 'Test', 'Delete'].includes(t!));
    expect(order).toEqual(['Save', 'Test', 'Delete']);
    expect(screen.getByRole('button', { name: 'Save' }).style.width).toBe('100%');
    const del = screen.getByRole('button', { name: 'Delete' });
    expect(del.style.color).toBe('var(--color-danger)');
    expect(del.style.marginTop).toBe('24px');
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    await waitFor(() => expect(where()).toBe('/settings/trackers'));
  });

  it('phone dialogs are bottom sheets', async () => {
    renderAt('/settings/trackers/6', false);
    await screen.findByTestId('tracker-form');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dlg = await screen.findByRole('dialog', { name: 'Delete Idx?' });
    expect(dlg.style.borderRadius).toBe('20px 20px 0 0');
  });
});

describe('Trackers polish (Designer, #22/#23)', () => {
  it('a failed Test keeps its specific error past 4 s, and an edit clears it', async () => {
    override = url => (url.endsWith('/api/trackers/test') ? res({ ok: false, error: 'Unauthorized' }) : null);
    renderAt('/settings/trackers/new?type=torznab');
    await screen.findByTestId('tracker-form');
    fill('Name', 'X');
    fill('URL', 'https://idx.example');
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.click(screen.getByRole('button', { name: 'Test' }));
    await waitFor(() => expect(screen.getByTestId('tracker-test-result').textContent).toBe('Unauthorized'));
    await act(async () => { vi.advanceTimersByTime(10000); });
    expect(screen.getByTestId('tracker-test-result').textContent).toBe('Unauthorized');
    fill('Name', 'Y');
    expect(screen.getByTestId('tracker-test-result').textContent).toBe('');
  });

  it('the form has a real Enabled switch, and turning it off is saved', async () => {
    db = [rec({ id: 7, name: 'Idx', type: 'torznab', api_key: MASK })];
    renderAt('/settings/trackers/7');
    await screen.findByTestId('tracker-form');
    const sw = screen.getByRole('switch', { name: 'Enabled' });
    expect(sw.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(sw);
    expect(sw.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(lastCall(c => c.method === 'PUT' && /\/api\/trackers\/7$/.test(c.url))?.body?.enabled).toBe(false));
    expect(JSON.stringify(calls)).not.toContain('"api_key":"********"');
  });

  it('phone: rows, inputs, buttons and category chips are all at least 44px tall', async () => {
    db = [rec({ id: 7, name: 'Idx', type: 'torznab', api_key: MASK })];
    renderAt('/settings/trackers', false);
    const row = (await screen.findAllByTestId('tracker-row'))[0]!;
    expect(row.style.height).toBe('56px');
    expect(screen.getByRole('switch', { name: 'Idx' }).style.height).toBe('44px');
    cleanup();
    renderAt('/settings/trackers/7', false);
    await screen.findByTestId('tracker-form');
    for (const chip of screen.getAllByTestId('tracker-category')) expect(chip.style.height).toBe('44px');
    expect(screen.getByTestId('tracker-save').style.height).toBe('44px');
    expect(screen.getByTestId('tracker-test').style.height).toBe('44px');
    expect(screen.getByTestId('tracker-delete').style.height).toBe('56px');
  });

  it('desktop keeps the 36px chips', async () => {
    renderAt('/settings/trackers/new?type=torznab');
    await screen.findByTestId('tracker-form');
    for (const chip of screen.getAllByTestId('tracker-category')) expect(chip.style.height).toBe('36px');
  });
});
