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
  it('desktop: cards with Default pill and status lines, + Add room', async () => {
    const calls: Call[] = []; stub([LR, BR, OF], calls);
    render(<RoomsSettings wide />);
    await waitFor(() => expect(screen.getAllByTestId('room-card')).toHaveLength(3));
    const cards = screen.getAllByTestId('room-card');
    expect(within(cards[0]!).getByTestId('room-default')).toBeTruthy();
    expect(cards[0]!.textContent).toContain('Online · Wake with ADB');
    expect(cards[1]!.textContent).toContain('Offline');
    expect(cards[2]!.textContent).toContain('Online · Wake-on-LAN');
    expect(screen.getByTestId('rooms-add').textContent).toBe('+ Add room');
    expect(writes(calls)).toEqual([]);
  });
  it('desktop ⋯ menu: Make default and Remove (confirmed)', async () => {
    const calls: Call[] = []; stub([LR, BR], calls);
    render(<RoomsSettings wide />);
    await waitFor(() => expect(screen.getAllByTestId('room-card')).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId('room-menu')[1]!);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Make default' }));
    await waitFor(() => expect(writes(calls)).toEqual([{ url: '/api/rooms/br', method: 'PUT', body: { default: true } }]));
    fireEvent.click(screen.getAllByTestId('room-menu')[0]!);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Remove' }));
    expect(screen.getByRole('alertdialog', { name: 'Remove Living Room?' })).toBeTruthy();
    fireEvent.click(screen.getByTestId('room-remove-go'));
    await waitFor(() => expect(screen.getAllByTestId('room-card')).toHaveLength(1));
    expect(writes(calls)[1]).toEqual({ url: '/api/rooms/lr', method: 'DELETE', body: undefined });
  });
  it('phone empty: No rooms yet, then Add Room wizard (blank name, no chips)', async () => {
    const calls: Call[] = []; stub([], calls);
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getByTestId('rooms-empty')).toBeTruthy());
    expect(screen.getByText('No rooms yet')).toBeTruthy();
    fireEvent.click(screen.getByTestId('rooms-empty-add'));
    expect(screen.getByRole('heading', { name: 'Add Room' })).toBeTruthy();
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
  it('phone edit: wake segments, fields per method, Default room, Save sends only changes', async () => {
    const calls: Call[] = []; stub([LR, BR], calls);
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getAllByTestId('room-row')).toHaveLength(2));
    fireEvent.click(screen.getAllByTestId('room-row')[1]!);
    expect(screen.getByRole('heading', { name: 'Bedroom' })).toBeTruthy();
    expect(['ADB', 'Wake-on-LAN', 'Harmony', 'Home Assistant', 'None'].every(l => screen.getByRole('radio', { name: l }))).toBe(true);
    expect(screen.getByTestId('room-port')).toBeTruthy();
    expect(screen.getByTestId('room-paired').textContent).toBe('Paired');
    const save = screen.getByTestId('room-save') as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(save.closest('header')).toBeTruthy();                       // Save sits in the title row (Designer)
    expect([save.style.opacity, save.style.height, save.style.minWidth, save.textContent]).toEqual(['0.4', '44px', '44px', 'Save']);
    expect(save.style.color).toBe('rgb(59, 130, 246)');
    const page = screen.getByTestId('room-edit');
    expect(page.lastElementChild!.getAttribute('data-testid')).toBe('room-remove-open');   // Remove room is the last item
    expect(screen.getAllByRole('button', { name: 'Save' })).toHaveLength(1);
    fireEvent.click(screen.getByTestId('room-wake-wol'));
    expect((screen.getByTestId('room-save') as HTMLButtonElement).style.opacity).toBe('1');
    expect(screen.queryByTestId('room-port')).toBeNull();
    fireEvent.change(screen.getByTestId('room-mac'), { target: { value: 'AA:BB:CC:DD:EE:FF' } });
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
    for (const s of ['Wake with', 'IP address', 'Default room', 'Remove room']) expect(text).toContain(s);
    expect(screen.getByTestId('room-wake-homeassistant')).toBeTruthy();
  });
});
