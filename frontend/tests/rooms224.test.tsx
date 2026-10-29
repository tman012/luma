/** v224 Settings > Rooms (Option A) and the Play-in choice. All network is mocked; nothing plays. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { RoomsSettings, playerLine } from '@/components/rooms/RoomsSettings';
import { PlayInPicker, PlayingToast, choosePlayRoom } from '@/components/rooms/PlayIn';
import { badge, type Room } from '@/components/rooms/roomsApi';

const P = (o: Partial<NonNullable<Room['player']>> = {}) => ({ id: 'd', name: 'SHIELD Android TV', product: 'Plex for Android (TV)', platform: 'android_tv',
  machine_id: 'mid-shield', host: '192.168.1.40', port: 5555, mac: '', harmony_activity: '', paired: true, online: true, wake: 'adb' as const, ...o });
const LR: Room = { id: 'lr', name: 'Living Room', default: true, harmony_hub: '', player: P() };
const BR: Room = { id: 'br', name: 'Bedroom', default: false, harmony_hub: '', player: P({ name: 'Apple TV 4K', product: 'Plex for Apple TV', machine_id: 'mid-atv', online: false }) };
const OF: Room = { id: 'of', name: 'Office', default: false, harmony_hub: '', player: P({ name: 'Kodi', product: 'Kodi', machine_id: 'k', wake: 'wol' }) };
const PLAYERS = [{ machine_id: 'mid-shield', name: 'SHIELD Android TV', product: 'Plex for Android (TV)', platform: 'Android', host: '192.168.1.40', online: true },
  { machine_id: 'mid-atv', name: 'Apple TV 4K', product: 'Plex for Apple TV', platform: 'tvOS', host: '', online: false }];

type Call = { url: string; method: string; body: unknown };
function stub(rooms: Room[], calls: Call[]) {
  let view = { rooms, default_room_id: rooms.find(r => r.default)?.id || '' };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url: String(url), method, body });
    if (String(url).endsWith('/players')) return new Response(JSON.stringify({ players: PLAYERS }), { status: 200 });
    if (method === 'POST' && String(url).endsWith('/connect')) {
      const pid = String(url).split('/').slice(-2)[0];
      const player = P({ name: body?.player?.name || body?.player?.host || 'Player', machine_id: body?.player?.machine_id || '', host: body?.player?.host || '', paired: true });
      view = { ...view, rooms: view.rooms.map(r => r.id === pid ? { ...r, player } : r) };
      return new Response(JSON.stringify({ ok: true, state: 'connected', hint: 'connected' }));
    }
    if (method === 'POST') {
      const r: Room = { id: 'new', name: body.name, default: !view.rooms.length, harmony_hub: '', player: null };
      view = { rooms: [...view.rooms, r], default_room_id: view.default_room_id || 'new' };
      return new Response(JSON.stringify(view), { status: 201 });
    }
    if (method === 'DELETE') { view = { ...view, rooms: view.rooms.filter(r => !String(url).endsWith('/' + r.id)) }; return new Response(JSON.stringify(view)); }
    if (method === 'PUT') {
      if (body.name === 'Bad') return new Response(JSON.stringify({ errors: { name: 'A room has this name' } }), { status: 400 });
      view = { ...view, rooms: view.rooms.map(r => String(url).endsWith('/' + r.id) ? { ...r, ...(body.name ? { name: body.name } : {}), ...(body.default ? { default: true } : {}) } : (body.default ? { ...r, default: false } : r)) };
      return new Response(JSON.stringify(view));
    }
    return new Response(JSON.stringify(view));
  }));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const writes = (calls: Call[]) => calls.filter(c => c.method !== 'GET');

describe('choosePlayRoom', () => {
  it('none / one plays at once / several pick with the default preselected', () => {
    expect(choosePlayRoom([])).toEqual({ mode: 'none' });
    expect(choosePlayRoom(null)).toEqual({ mode: 'none' });
    expect(choosePlayRoom([{ ...LR, player: null }])).toEqual({ mode: 'none' });
    expect(choosePlayRoom([LR])).toEqual({ mode: 'auto', room: LR });
    expect(choosePlayRoom([LR, { ...BR, player: null }])).toEqual({ mode: 'auto', room: LR });
    expect(choosePlayRoom([BR, LR, OF])).toMatchObject({ mode: 'pick', preselect: 'lr' });
    expect(choosePlayRoom([BR, LR, OF], 'of')).toMatchObject({ mode: 'pick', preselect: 'of' });
    expect(choosePlayRoom([{ ...BR }, { ...OF }])).toMatchObject({ mode: 'pick', preselect: 'br' });
  });
  it('badges and status lines', () => {
    expect([badge(LR.player), badge(BR.player), badge(OF.player)]).toEqual(['ATV', 'tv', 'K']);
    expect(playerLine(LR)).toBe('Online · Wake with ADB');
    expect(playerLine(BR)).toBe('Offline');
    expect(playerLine(LR, true)).toBe('Online · SHIELD Android TV');
  });
});

describe('PlayInPicker and PlayingToast', () => {
  it('Play in: default preselected, pick another, Cancel', () => {
    const onPick = vi.fn(); const onCancel = vi.fn();
    render(<PlayInPicker rooms={[LR, BR, OF]} preselect="lr" onPick={onPick} onCancel={onCancel} />);
    expect(screen.getByRole('dialog', { name: 'Play in' })).toBeTruthy();
    const radios = screen.getAllByTestId('play-in-room');
    expect(radios.map(r => r.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
    fireEvent.click(radios[2]!);
    fireEvent.click(screen.getByTestId('play-in-go'));
    expect(onPick).toHaveBeenCalledWith(OF);
    fireEvent.click(screen.getByTestId('play-in-cancel'));
    expect(onCancel).toHaveBeenCalled();
  });
  it('Shuffle in title and action', () => {
    render(<PlayInPicker rooms={[LR, BR]} preselect="br" action="Shuffle" onPick={() => {}} onCancel={() => {}} />);
    expect(screen.getByRole('dialog', { name: 'Shuffle in' })).toBeTruthy();
    expect(screen.getByTestId('play-in-go').textContent).toBe('Shuffle');
  });
  it("toast reads 'Playing in <Room>' with Change", () => {
    const onChange = vi.fn();
    render(<PlayingToast room={LR} onChange={onChange} />);
    expect(screen.getByTestId('playing-toast').textContent).toBe('Playing in Living RoomChange');
    fireEvent.click(screen.getByTestId('playing-toast-change'));
    expect(onChange).toHaveBeenCalled();
  });
});

describe('Settings > Rooms', () => {
  it('premium pass desktop: centered Rooms, bubbly tiles with icon, pencil and TV chip, then the Add room tile', async () => {
    const calls: Call[] = []; stub([LR, { ...BR, icon: 'bed' }, { ...OF, player: null }], calls);
    render(<RoomsSettings wide />);
    await waitFor(() => expect(screen.getAllByTestId('room-card')).toHaveLength(3));
    const cards = screen.getAllByTestId('room-card');
    expect(cards.map(c => c.getAttribute('data-icon'))).toEqual(['home', 'bed', 'home']);   // missing icon → home
    expect(cards.map(c => within(c).getByTestId('room-tv-chip').textContent)).toEqual(['SHIELD Android TV', 'Apple TV 4K', 'No TV']);
    expect(screen.getAllByTestId('room-icon-edit')).toHaveLength(3);
    expect(screen.getByRole('heading', { level: 2, name: 'Rooms' }).style.textAlign).toBe('center');
    const grid = screen.getByTestId('rooms-grid');
    expect(grid.lastElementChild!.getAttribute('data-testid')).toBe('rooms-add');
    expect(grid.style.gridTemplateColumns).toBe('repeat(auto-fit, 236px)');   // Designer: 1–2 tiles center under the heading
    expect(grid.style.justifyContent).toBe('center');
    expect(screen.getByTestId('rooms-add').textContent).toBe('Add room');
    expect(screen.queryByTestId('room-menu')).toBeNull();
    expect(writes(calls)).toEqual([]);
  });
  it('premium pass: the pencil opens the icon picker; Cancel saves nothing, Done saves the icon', async () => {
    const calls: Call[] = []; stub([LR, BR], calls);
    render(<RoomsSettings wide />);
    await waitFor(() => expect(screen.getAllByTestId('room-card')).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId('room-icon-edit')[1]!);
    const picker = screen.getByTestId('icon-picker');
    expect(within(picker).getAllByRole('radio')).toHaveLength(15);
    expect(within(picker).getByTestId('icon-home').getAttribute('aria-checked')).toBe('true');
    expect(picker.textContent).toBe('BedroomCancelDone');                  // no text besides the room name
    fireEvent.click(within(picker).getByTestId('icon-bed'));
    fireEvent.click(screen.getByTestId('icon-picker-cancel'));
    expect(screen.queryByTestId('icon-picker')).toBeNull();
    expect(writes(calls)).toEqual([]);
    fireEvent.click(screen.getAllByTestId('room-icon-edit')[1]!);
    fireEvent.click(screen.getByTestId('icon-bed'));
    fireEvent.click(screen.getByTestId('icon-picker-done'));
    await waitFor(() => expect(writes(calls)).toEqual([{ url: '/api/rooms/br', method: 'PUT', body: { icon: 'bed' } }]));
    expect(screen.queryByTestId('room-edit')).toBeNull();                  // the pencil never opens the room page
  });
  it('premium pass desktop: a tile opens the centered room page; Remove room confirms', async () => {
    const calls: Call[] = []; stub([LR, BR], calls);
    render(<RoomsSettings wide />);
    await waitFor(() => expect(screen.getAllByTestId('room-card')).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId('room-open')[0]!);
    const page = screen.getByTestId('room-edit');
    expect(page.style.maxWidth).toBe('600px');
    expect(screen.getByRole('heading', { level: 2, name: 'Living Room' })).toBeTruthy();
    expect(screen.queryByTestId('room-save')).toBeNull();                 // Save shows only when dirty
    fireEvent.click(screen.getByTestId('room-remove-open'));
    expect(screen.getByRole('alertdialog', { name: 'Remove Living Room?' })).toBeTruthy();
    fireEvent.click(screen.getByTestId('room-remove-go'));
    await waitFor(() => expect(screen.getAllByTestId('room-card')).toHaveLength(1));
    expect(writes(calls)).toEqual([{ url: '/api/rooms/lr', method: 'DELETE', body: undefined }]);
  });
  it('phone empty: No rooms yet, then Add Room wizard (blank name, no chips)', async () => {
    const calls: Call[] = []; stub([], calls);
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getByTestId('rooms-empty')).toBeTruthy());
    expect(screen.getByText('No rooms yet')).toBeTruthy();
    fireEvent.click(screen.getByTestId('rooms-empty-add'));
    expect(screen.getByRole('heading', { name: 'Name this room' })).toBeTruthy();
    expect(screen.queryByTestId('room-chip')).toBeNull();
    expect((screen.getByTestId('room-name') as HTMLInputElement).value).toBe('');
    expect((screen.getByTestId('room-next') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId('room-name'), { target: { value: 'Family Room' } });
    fireEvent.click(screen.getByTestId('room-next'));
    await waitFor(() => expect(screen.getByTestId('wiz-adb-howto')).toBeTruthy());
    expect(writes(calls)).toEqual([{ url: '/api/rooms', method: 'POST', body: { name: 'Family Room' } }]);
  });
  it('phone: Add player by IP pairs then lists', async () => {
    const empty: Room = { id: 'fr', name: 'Family Room', default: true, harmony_hub: '', player: null };
    const calls: Call[] = []; stub([empty], calls);
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getAllByTestId('room-row')).toHaveLength(1));
    fireEvent.click(screen.getAllByTestId('room-row')[0]!);
    fireEvent.click(screen.getByTestId('room-add-player'));
    fireEvent.click(screen.getByTestId('room-by-ip'));
    fireEvent.change(screen.getByTestId('room-ip'), { target: { value: '192.168.1.77' } });
    fireEvent.click(screen.getByTestId('room-pick-go'));
    await waitFor(() => expect(writes(calls).some(c => c.url.endsWith('/connect'))).toBe(true));
    const connect = writes(calls).find(c => String(c.url).endsWith('/connect'))!;
    expect(connect).toMatchObject({ method: 'POST', body: { player: { host: '192.168.1.77' } } });
  });
  it('phone edit: Power chips, Wake-on-LAN under Advanced (MAC auto-detected), Default room, Save sends only changes', async () => {
    const calls: Call[] = []; stub([LR, BR], calls);
    const base = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>);
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => String(url).endsWith('/mac')
      ? new Response(JSON.stringify({ mac: 'AA:BB:CC:DD:EE:FF' })) : base(url, init)));
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getAllByTestId('room-row')).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId('room-row')[1]!);
    expect(screen.getByRole('heading', { name: 'Bedroom' })).toBeTruthy();
    expect(['TV', 'Home Assistant', 'Harmony', 'Off'].every(l => screen.getByRole('radio', { name: l }))).toBe(true);
    expect(screen.getByRole('radio', { name: 'TV' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('room-paired').textContent).toBe('Paired');
    const save = screen.getByTestId('room-save') as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(save.closest('header')).toBeTruthy();                       // Save sits in the title row (Designer)
    expect([save.style.opacity, save.style.height, save.style.minWidth, save.textContent]).toEqual(['0.4', '44px', '44px', 'Save']);
    const page = screen.getByTestId('room-edit');
    expect(page.lastElementChild!.getAttribute('data-testid')).toBe('room-remove-open');   // Remove room is the last item
    expect(screen.getAllByRole('button', { name: 'Save' })).toHaveLength(1);
    expect(screen.queryByTestId('room-port')).toBeNull();               // Advanced starts collapsed
    fireEvent.click(screen.getByTestId('room-advanced-toggle'));
    expect(screen.getByTestId('room-port')).toBeTruthy();
    fireEvent.click(screen.getByRole('switch', { name: 'Wake-on-LAN' }));
    await waitFor(() => expect((screen.getByTestId('room-mac') as HTMLInputElement).value).toBe('AA:BB:CC:DD:EE:FF'));
    expect((screen.getByTestId('room-save') as HTMLButtonElement).style.opacity).toBe('1');
    fireEvent.click(screen.getByRole('switch', { name: 'Default room' }));
    fireEvent.click(screen.getByTestId('room-save'));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0]).toEqual({ url: '/api/rooms/br', method: 'PUT', body: { wake: 'wol', mac: 'AA:BB:CC:DD:EE:FF', default: true } });
    await waitFor(() => expect(screen.getAllByTestId('room-row')).toHaveLength(2));
  });
  it('phone edit: save error stays on the page; Remove room confirms', async () => {
    const calls: Call[] = []; stub([LR, BR], calls);
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getAllByTestId('room-row')).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId('room-row')[0]!);
    fireEvent.change(screen.getByTestId('room-name'), { target: { value: 'Bad' } });
    fireEvent.click(screen.getByTestId('room-save'));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('A room has this name'));
    fireEvent.click(screen.getByTestId('room-remove-open'));
    fireEvent.click(screen.getByTestId('room-remove-go'));
    await waitFor(() => expect(screen.getAllByTestId('room-row')).toHaveLength(1));
  });
  it('change player from the edit page pairs via connect', async () => {
    const calls: Call[] = []; stub([LR], calls);
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getAllByTestId('room-row')).toHaveLength(1));
    fireEvent.click(screen.getAllByTestId('room-row')[0]!);
    fireEvent.click(screen.getByTestId('room-change-player'));
    await waitFor(() => expect(screen.getAllByTestId('room-player').length).toBeGreaterThan(0));
    fireEvent.click(screen.getAllByTestId('room-player')[0]!);
    fireEvent.click(screen.getByTestId('room-pick-go'));
    await waitFor(() => expect(writes(calls).some(c => String(c.url).endsWith('/connect'))).toBe(true));
    await waitFor(() => expect(screen.getByTestId('room-edit')).toBeTruthy());
  });
  it('no helper text and sentence case labels (the A mocks)', async () => {
    stub([LR], []);
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getAllByTestId('room-row')).toHaveLength(1));
    fireEvent.click(screen.getAllByTestId('room-row')[0]!);
    const text = screen.getByTestId('room-edit').textContent || '';
    for (const s of ['Power', 'Name', 'Default room', 'Advanced', 'Remove room']) expect(text).toContain(s);
    expect(screen.getByRole('textbox', { name: 'IP address' })).toBeTruthy();
    expect(screen.getByTestId('room-wake-homeassistant')).toBeTruthy();
  });
});
