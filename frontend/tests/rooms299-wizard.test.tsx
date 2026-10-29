/** v299 Rooms Add wizard: empty name, no chips, Pair-before-persist, Cancel deletes empty room,
 * Pair→Back→Back keeps paired room, HA/Harmony branches, helper text only on Network debugging. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RoomsSettings } from '@/components/rooms/RoomsSettings';
import type { Room } from '@/components/rooms/roomsApi';

type Call = { url: string; method: string; body: unknown };

function stub(rooms: Room[], calls: Call[], opts?: { pairOk?: boolean; pairHint?: string; haSet?: boolean }) {
  let view = { rooms: [...rooms], default_room_id: rooms.find(r => r.default)?.id || '' };
  const pairOk = opts?.pairOk !== false;
  const haSet = opts?.haSet === true;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const u = String(url);
    calls.push({ url: u, method, body });
    if (u.includes('/connectors/homeassistant')) {
      if (method === 'PUT') {
        return new Response(JSON.stringify({ url: body.url || '', access_token: { set: !!body.access_token || haSet, last4: '1234' } }), { status: 200 });
      }
      return new Response(JSON.stringify({ url: haSet ? 'http://ha.local:8123' : '', access_token: { set: haSet, last4: haSet ? '1234' : '' } }), { status: 200 });
    }
    if (u.endsWith('/players')) return new Response(JSON.stringify({ players: [] }), { status: 200 });
    if (u.endsWith('/options/ha')) return new Response(JSON.stringify({ entities: [{ id: 'media_player.living_room_tv', name: 'Living Room TV' }, { id: 'switch.tv_power', name: 'TV power' }] }));
    if (u.includes('/options/harmony')) return new Response(JSON.stringify({ ok: true, activities: ['Watch TV', 'SHIELD TV'], devices: ['AV Receiver', 'NVIDIA Shield'] }));
    if (method === 'POST' && u.endsWith('/connect')) {
      if (!pairOk) return new Response(JSON.stringify({ ok: false, state: 'failed', hint: opts?.pairHint || 'wrong_port' }), { status: 200 });
      const pid = u.split('/').slice(-2)[0];
      const host = body?.player?.host || '';
      view = {
        ...view,
        rooms: view.rooms.map(r => r.id === pid ? {
          ...r,
          player: {
            id: 'd', name: host || 'Player', product: '', platform: 'android_tv', machine_id: '',
            host, port: 5555, mac: '', harmony_activity: '', paired: true, online: false, wake: 'adb' as const,
          },
        } : r),
      };
      return new Response(JSON.stringify({ ok: true, state: 'connected', hint: 'connected' }));
    }
    if (method === 'POST' && u.endsWith('/api/rooms')) {
      const r: Room = { id: 'new', name: body.name, default: !view.rooms.length, harmony_hub: '', player: null };
      view = { rooms: [...view.rooms, r], default_room_id: view.default_room_id || 'new' };
      return new Response(JSON.stringify(view), { status: 201 });
    }
    if (method === 'DELETE') {
      view = { ...view, rooms: view.rooms.filter(r => !u.endsWith('/' + r.id)) };
      return new Response(JSON.stringify(view));
    }
    if (method === 'PUT') {
      const id = u.split('/').pop()!;
      view = {
        ...view,
        rooms: view.rooms.map(r => {
          if (r.id !== id) return r;
          const player = r.player ? {
            ...r.player,
            ...(body.wake != null ? { wake: body.wake } : {}),
            ...(body.ha_power_entity != null ? { ha_power_entity: body.ha_power_entity } : {}),
            ...(body.harmony_activity != null ? { harmony_activity: body.harmony_activity } : {}),
            ...(body.harmony_nav_device != null ? { harmony_nav_device: body.harmony_nav_device } : {}),
          } : r.player;
          return {
            ...r,
            ...(body.harmony_hub != null ? { harmony_hub: body.harmony_hub } : {}),
            ...(body.harmony_volume_device != null ? { harmony_volume_device: body.harmony_volume_device } : {}),
            player,
          };
        }),
      };
      return new Response(JSON.stringify(view));
    }
    return new Response(JSON.stringify(view));
  }));
}

const writes = (calls: Call[]) => calls.filter(c => c.method !== 'GET');
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function startWizard(name = 'Family Room') {
  render(<RoomsSettings wide={false} />);
  await waitFor(() => expect(screen.getByTestId('rooms-empty')).toBeTruthy());
  fireEvent.click(screen.getByTestId('rooms-empty-add'));
  expect(screen.getByRole('heading', { name: 'Name this room' })).toBeTruthy();
  expect(screen.queryByTestId('room-chip')).toBeNull();
  expect((screen.getByTestId('room-name') as HTMLInputElement).value).toBe('');
  expect((screen.getByTestId('room-next') as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByTestId('room-name'), { target: { value: name } });
  fireEvent.click(screen.getByTestId('room-next'));
  await waitFor(() => expect(screen.getByTestId('wiz-adb-howto')).toBeTruthy());
}

describe('v299 Rooms Add wizard', () => {
  it('step machine: blank name, no chips, Next disabled until name', async () => {
    const calls: Call[] = []; stub([], calls);
    await startWizard('Den');
    expect(writes(calls)[0]).toEqual({ url: '/api/rooms', method: 'POST', body: { name: 'Den' } });
    const post = writes(calls).find(c => c.method === 'POST' && c.url === '/api/rooms')!;
    expect(post.body).toEqual({ name: 'Den' });
    expect((post.body as { devices?: unknown }).devices).toBeUndefined();
  });

  it('helper instructional copy only on Network debugging', async () => {
    const calls: Call[] = []; stub([], calls);
    await startWizard();
    expect(screen.getByTestId('wiz-adb-steps').textContent).toContain('Network debugging');
    expect(screen.getByTestId('wiz-adb-steps').textContent).toContain('Select Build 7 times');
    expect(screen.getByTestId('wiz-adb-steps').querySelectorAll('li')).toHaveLength(5);
    fireEvent.click(screen.getByTestId('wiz-adb-continue'));
    await waitFor(() => expect(screen.getByTestId('wiz-player-step')).toBeTruthy());
    const playerText = (screen.getByTestId('wiz-player-step').textContent || '').toLowerCase();
    expect(playerText).not.toContain('build number');
    expect(playerText).not.toContain('developer options');
    expect(screen.queryByTestId('wiz-adb-steps')).toBeNull();
  });

  it('Pair-before-persist: no device until Pair OK; fail shows pairFailLabel only', async () => {
    const calls: Call[] = []; stub([], calls, { pairOk: false, pairHint: 'wrong_port' });
    await startWizard();
    fireEvent.click(screen.getByTestId('wiz-adb-continue'));
    fireEvent.change(screen.getByTestId('room-ip'), { target: { value: '192.168.1.108' } });
    fireEvent.click(screen.getByTestId('room-pair'));
    await waitFor(() => expect(screen.getByTestId('wiz-pair-fail').textContent).toBe("Couldn't reach ADB on that port"));
    expect(screen.queryByTestId('wiz-pair-ok')).toBeNull();
    expect(screen.queryByTestId('wiz-player-continue')).toBeNull();          // the button is back to Pair
    expect(screen.getByTestId('room-pair').textContent).toBe('Pair');
    const connect = writes(calls).find(c => String(c.url).endsWith('/connect'))!;
    expect(connect.body).toEqual({ player: { host: '192.168.1.108' } });
    // room still empty in view — reload list would show no player; continue disabled proves no success
  });

  it('Pair success enables Continue; busy shows Allow prompt', async () => {
    const calls: Call[] = [];
    let resolvePair: (v: unknown) => void;
    const pairPromise = new Promise(r => { resolvePair = r; });
    let view = { rooms: [] as Room[], default_room_id: '' };
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const u = String(url);
      calls.push({ url: u, method, body });
      if (u.includes('/connectors/homeassistant')) {
        return new Response(JSON.stringify({ url: '', access_token: { set: false, last4: '' } }), { status: 200 });
      }
      if (method === 'POST' && u.endsWith('/api/rooms')) {
        const r: Room = { id: 'new', name: body.name, default: true, harmony_hub: '', player: null };
        view = { rooms: [r], default_room_id: 'new' };
        return new Response(JSON.stringify(view), { status: 201 });
      }
      if (method === 'POST' && u.endsWith('/connect')) {
        await pairPromise;
        view = {
          rooms: [{
            id: 'new', name: 'Family Room', default: true, harmony_hub: '',
            player: {
              id: 'd', name: '192.168.1.50', product: '', platform: 'android_tv', machine_id: '',
              host: '192.168.1.50', port: 5555, mac: '', harmony_activity: '', paired: true, online: true, wake: 'adb',
            },
          }],
          default_room_id: 'new',
        };
        return new Response(JSON.stringify({ ok: true, state: 'connected', hint: 'connected' }));
      }
      if (method === 'PUT') return new Response(JSON.stringify(view));
      return new Response(JSON.stringify(view));
    }));
    render(<RoomsSettings wide={false} />);
    await waitFor(() => expect(screen.getByTestId('rooms-empty')).toBeTruthy());
    fireEvent.click(screen.getByTestId('rooms-empty-add'));
    fireEvent.change(screen.getByTestId('room-name'), { target: { value: 'Family Room' } });
    fireEvent.click(screen.getByTestId('room-next'));
    await waitFor(() => expect(screen.getByTestId('wiz-adb-howto')).toBeTruthy());
    fireEvent.click(screen.getByTestId('wiz-adb-continue'));
    fireEvent.change(screen.getByTestId('room-ip'), { target: { value: '192.168.1.50' } });
    fireEvent.click(screen.getByTestId('room-pair'));
    expect(screen.getByTestId('wiz-pair-busy').textContent).toBe('Waiting for TV');
    expect(screen.getByRole('heading', { name: 'Allow on your TV' })).toBeTruthy();
    expect(screen.getByTestId('room-pair').textContent).toBe('Pairing…');
    expect((screen.getByTestId('room-pair') as HTMLButtonElement).disabled).toBe(true);
    resolvePair!(null);
    await waitFor(() => expect(screen.getByTestId('wiz-pair-ok')).toBeTruthy());
    expect((screen.getByTestId('wiz-player-continue') as HTMLButtonElement).disabled).toBe(false);
  });

  it('Cancel/Back before Pair deletes empty room', async () => {
    const calls: Call[] = []; stub([], calls);
    await startWizard('Family Room');
    fireEvent.click(screen.getByLabelText('Back'));
    await waitFor(() => expect(writes(calls).some(c => c.method === 'DELETE')).toBe(true));
    expect(writes(calls).find(c => c.method === 'DELETE')!.url).toBe('/api/rooms/new');
    await waitFor(() => expect(screen.getByTestId('rooms-empty')).toBeTruthy());
  });

  it('Pair then Back then Back does not DELETE paired room', async () => {
    const calls: Call[] = []; stub([], calls);
    await startWizard('Family Room');
    fireEvent.click(screen.getByTestId('wiz-adb-continue'));
    fireEvent.change(screen.getByTestId('room-ip'), { target: { value: '192.168.1.50' } });
    fireEvent.click(screen.getByTestId('room-pair'));
    await waitFor(() => expect(screen.getByTestId('wiz-pair-ok')).toBeTruthy());
    // Back from player → Network debugging howto
    fireEvent.click(screen.getByLabelText('Back'));
    await waitFor(() => expect(screen.getByTestId('wiz-adb-howto')).toBeTruthy());
    // Back from howto — must navigate without DELETE (player present)
    fireEvent.click(screen.getByLabelText('Back'));
    await waitFor(() => expect(screen.getByTestId('rooms-list')).toBeTruthy());
    expect(writes(calls).some(c => c.method === 'DELETE')).toBe(false);
    expect(screen.getByTestId('room-row').textContent).toContain('Family Room');
  });

  it('Smart home No finishes with wake=adb', async () => {
    const calls: Call[] = []; stub([], calls);
    await startWizard();
    fireEvent.click(screen.getByTestId('wiz-adb-continue'));
    fireEvent.change(screen.getByTestId('room-ip'), { target: { value: '192.168.1.50' } });
    fireEvent.click(screen.getByTestId('room-pair'));
    await waitFor(() => expect(screen.getByTestId('wiz-pair-ok')).toBeTruthy());
    fireEvent.click(screen.getByTestId('wiz-player-continue'));
    expect((screen.getByTestId('wiz-smarthome-continue') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId('wiz-smarthome-no'));
    expect(writes(calls).some(c => c.method === 'PUT')).toBe(false);            // a card only selects
    fireEvent.click(screen.getByTestId('wiz-smarthome-continue'));
    await waitFor(() => expect(writes(calls).some(c => c.method === 'PUT' && (c.body as { wake?: string }).wake === 'adb')).toBe(true));
  });

  it('HA branch: connectors inline when missing, then ha_power_entity', async () => {
    const calls: Call[] = []; stub([], calls, { haSet: false });
    await startWizard();
    fireEvent.click(screen.getByTestId('wiz-adb-continue'));
    fireEvent.change(screen.getByTestId('room-ip'), { target: { value: '192.168.1.50' } });
    fireEvent.click(screen.getByTestId('room-pair'));
    await waitFor(() => expect(screen.getByTestId('wiz-pair-ok')).toBeTruthy());
    fireEvent.click(screen.getByTestId('wiz-player-continue'));
    fireEvent.click(screen.getByTestId('wiz-smarthome-yes'));
    fireEvent.click(screen.getByTestId('wiz-smarthome-continue'));
    fireEvent.click(screen.getByTestId('wiz-power-ha'));
    fireEvent.click(screen.getByTestId('wiz-power-continue'));
    await waitFor(() => expect(screen.getByTestId('wiz-ha-connectors')).toBeTruthy());
    fireEvent.change(screen.getByTestId('ha-url'), { target: { value: 'http://ha.local:8123' } });
    fireEvent.change(screen.getByTestId('ha-access-token'), { target: { value: 'tokensecret' } });
    fireEvent.click(screen.getByTestId('wiz-ha-connect'));
    await waitFor(() => expect(writes(calls).some(c => c.url.includes('/connectors/homeassistant') && c.method === 'PUT')).toBe(true));
    await waitFor(() => expect(screen.getByTestId('room-ha-entity').querySelectorAll('option').length).toBe(3));
    expect(screen.getByTestId('room-ha-entity').tagName).toBe('SELECT');          // a picker, not raw text
    expect((screen.getByTestId('wiz-ha-done') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId('room-ha-entity'), { target: { value: 'switch.tv_power' } });
    fireEvent.click(screen.getByTestId('wiz-ha-done'));
    await waitFor(() => expect(writes(calls).some(c => c.method === 'PUT' && (c.body as { wake?: string }).wake === 'homeassistant')).toBe(true));
    const put = writes(calls).find(c => c.method === 'PUT' && (c.body as { wake?: string }).wake === 'homeassistant')!;
    expect(put.body).toMatchObject({ wake: 'homeassistant', ha_power_entity: 'switch.tv_power' });
  });

  it('Harmony branch saves v288 fields', async () => {
    const calls: Call[] = []; stub([], calls);
    await startWizard();
    fireEvent.click(screen.getByTestId('wiz-adb-continue'));
    fireEvent.change(screen.getByTestId('room-ip'), { target: { value: '192.168.1.50' } });
    fireEvent.click(screen.getByTestId('room-pair'));
    await waitFor(() => expect(screen.getByTestId('wiz-pair-ok')).toBeTruthy());
    fireEvent.click(screen.getByTestId('wiz-player-continue'));
    fireEvent.click(screen.getByTestId('wiz-smarthome-yes'));
    fireEvent.click(screen.getByTestId('wiz-smarthome-continue'));
    fireEvent.click(screen.getByTestId('wiz-power-harmony'));
    fireEvent.click(screen.getByTestId('wiz-power-continue'));
    fireEvent.change(screen.getByTestId('room-hub'), { target: { value: '192.168.1.19' } });
    await waitFor(() => expect(screen.getByTestId('room-activity').querySelectorAll('option').length).toBe(3), { timeout: 2000 });
    fireEvent.change(screen.getByTestId('room-activity'), { target: { value: 'SHIELD TV' } });
    fireEvent.change(screen.getByTestId('room-volume-device'), { target: { value: 'AV Receiver' } });
    expect(screen.queryByTestId('room-nav-device')).toBeNull();                   // Navigation lives under the room's Advanced
    fireEvent.click(screen.getByTestId('wiz-harmony-done'));
    await waitFor(() => expect(writes(calls).some(c => c.method === 'PUT' && (c.body as { wake?: string }).wake === 'harmony')).toBe(true));
    const put = writes(calls).find(c => c.method === 'PUT' && (c.body as { wake?: string }).wake === 'harmony')!;
    expect(put.body).toMatchObject({
      wake: 'harmony',
      harmony_hub: '192.168.1.19',
      harmony_activity: 'SHIELD TV',
      harmony_volume_device: 'AV Receiver',
      harmony_nav_device: '',
    });
  });

  it('premium pass desktop: one centered 620px card — no Back on step 1, 5 progress dots, the name step is an empty field only', async () => {
    const calls: Call[] = []; stub([], calls);
    render(<RoomsSettings wide />);
    await waitFor(() => expect(screen.getByTestId('rooms-add')).toBeTruthy());
    fireEvent.click(screen.getByTestId('rooms-add'));
    const card = screen.getByTestId('room-flow');
    expect(card.style.width).toBe('620px');
    expect(card.getAttribute('data-step')).toBe('1');
    expect(screen.queryByTestId('wiz-back')).toBeNull();
    expect(screen.getByTestId('wiz-dots').children).toHaveLength(5);
    expect(screen.getByRole('heading', { name: 'Name this room' })).toBeTruthy();
    const name = screen.getByTestId('room-name') as HTMLInputElement;
    expect([name.value, name.placeholder, name.getAttribute('autocomplete')]).toEqual(['', '', 'off']);
    expect(screen.getByTestId('room-name-step').querySelectorAll('input')).toHaveLength(1);
    expect(screen.getByTestId('room-name-step').querySelectorAll('svg, img')).toHaveLength(0);   // no icons on the name step
    expect(screen.queryByTestId('room-chip')).toBeNull();
    expect(screen.queryByTestId('icon-picker')).toBeNull();
    expect(screen.queryAllByRole('radio')).toHaveLength(0);                      // no icon choices on this step
    expect(screen.getByTestId('room-name-step').textContent).toBe('Name this roomContinue');
    const next = screen.getByTestId('room-next') as HTMLButtonElement;
    expect([next.textContent, next.disabled]).toEqual(['Continue', true]);
    fireEvent.change(name, { target: { value: '   ' } });
    expect(next.disabled).toBe(true);                                             // spaces only stay disabled
    fireEvent.change(name, { target: { value: 'Den' } });
    expect(next.disabled).toBe(false);
    fireEvent.change(name, { target: { value: '' } });
    expect(next.disabled).toBe(true);
    expect(writes(calls)).toEqual([]);
    fireEvent.change(name, { target: { value: 'Den' } });
    fireEvent.click(next);
    await waitFor(() => expect(screen.getByTestId('room-flow').getAttribute('data-step')).toBe('2'));
    expect(screen.getByTestId('wiz-back')).toBeTruthy();
    expect(writes(calls)).toEqual([{ url: '/api/rooms', method: 'POST', body: { name: 'Den' } }]);   // no icon sent: the server defaults to home
  });
});
