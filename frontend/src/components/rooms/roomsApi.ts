/** v224 Settings > Rooms (Option A): /api/rooms (reads signed in; writes the signed-in owner).
 * v272: create is empty-room only; Pair (POST .../connect) persists the player on success. */
import { apiFetch } from '@/apiClient';

export type Wake = 'adb' | 'wol' | 'harmony' | 'none';
export type RoomPlayer = {
  id: string; name: string; product: string; platform: string; machine_id: string; host: string;
  port: number | null; mac: string; harmony_activity: string; harmony_nav_device?: string; paired: boolean; online: boolean; wake: Wake;
};
/** v231 (Reviewer): a signed-in non-owner gets only id, name, default and online (no harmony_hub or player). */
export type Room = { id: string; name: string; default: boolean; online?: boolean; harmony_hub?: string; harmony_volume_device?: string; player?: RoomPlayer | null };
export const roomOnline = (r: Pick<Room, 'online' | 'player'>) => !!(r.online ?? r.player?.online);
export type RoomsView = { rooms: Room[]; default_room_id: string };
export type FoundPlayer = { machine_id: string; name: string; product: string; platform: string; host: string; online: boolean };
export type R<T> = { ok: true; data: T } | { ok: false; status: number; errors: Record<string, string> };

export const ROOMS_URL = '/api/rooms';
export const SAVE_FAILED = "Couldn't save";
const okView = (d: unknown): d is RoomsView => !!d && Array.isArray((d as RoomsView).rooms);

async function call<T>(url: string, init: RequestInit | undefined, valid: (d: unknown) => boolean): Promise<R<T>> {
  try {
    const res = await apiFetch(url, init);
    const d = await res.json().catch(() => ({}));
    if (res.ok && valid(d)) return { ok: true, data: d as T };
    return { ok: false, status: res.status, errors: (d && d.errors) || { _: SAVE_FAILED } };
  } catch {
    return { ok: false, status: 0, errors: { _: SAVE_FAILED } };
  }
}
const json = (method: string, body?: unknown): RequestInit =>
  ({ method, headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

export const loadRooms = () => call<RoomsView>(ROOMS_URL, undefined, okView);
export const loadPlayers = () => call<{ players: FoundPlayer[] }>(`${ROOMS_URL}/players`, undefined, d => !!d && Array.isArray((d as { players: unknown }).players));
/** v272: empty room — name only. */
export const createRoom = (name: string) => call<RoomsView>(ROOMS_URL, json('POST', { name }), okView);
export type RoomPatch = Partial<{ name: string; wake: Wake; host: string; port: number | string; mac: string;
  harmony_hub: string; harmony_activity: string; harmony_volume_device: string; harmony_nav_device: string; default: boolean }>;
export const updateRoom = (id: string, patch: RoomPatch) => call<RoomsView>(`${ROOMS_URL}/${encodeURIComponent(id)}`, json('PUT', patch), okView);

/** v265/v272 Pair: prod's ADB flow. Optional player = Add device (persists only on success). */
export type PairResult = { ok: boolean; state: 'connected' | 'unauthorized' | 'failed'; hint?: string; detail?: string };
/** UI strings for connect hints (Taylor saw only "Pairing failed" — surface the real cause). */
export const PAIR_FAIL: Record<string, string> = {
  wrong_port: "Couldn't reach ADB on that port",
  unreachable: "Couldn't reach that player",
  unauthorized: 'Allow network debugging on the TV',
  timeout: 'Pairing timed out',
  adb_disabled: 'ADB is disabled on this install',
  adb_missing: 'ADB is not available',
  pair_failed: 'Pairing failed',
  needs_pairing: 'Allow network debugging on the TV',
  refused: "Couldn't reach ADB on that port",
  unknown: 'Pairing failed',
};
export function pairFailLabel(hint?: string): string {
  if (hint && Object.prototype.hasOwnProperty.call(PAIR_FAIL, hint)) {
    return PAIR_FAIL[hint] as string;
  }
  return 'Pairing failed';
}

export async function pairRoom(id: string, player?: Partial<FoundPlayer>, port?: number | string): Promise<PairResult> {
  try {
    const body: Record<string, unknown> = {};
    if (player) body.player = player;
    if (port != null && port !== '') body.port = port;
    const res = await apiFetch(`${ROOMS_URL}/${encodeURIComponent(id)}/connect`, json('POST', body));
    const d = await res.json().catch(() => ({}));
    const state = d.state === 'connected' || d.state === 'unauthorized' ? d.state : 'failed';
    return { ok: res.ok && !!d.ok, state, hint: d.hint, detail: d.detail };
  } catch {
    return { ok: false, state: 'failed', hint: 'unknown' };
  }
}
export const removeRoom = (id: string) => call<RoomsView>(`${ROOMS_URL}/${encodeURIComponent(id)}`, json('DELETE'), okView);

/** A player's two- or three-letter badge, as the A mocks: ATV for Android TV, tv for Apple TV, else the initial. */
export function badge(p: { product?: string; platform?: string; name?: string } | null | undefined): string {
  if (!p) return '?';
  const s = (p?.product || `${p?.platform || ''} ${p?.name || ''}`).toLowerCase();   // the Plex product wins
  if (s.includes('android') || s.includes('shield')) return 'ATV';
  if (s.includes('apple') || s.includes('tvos')) return 'tv';
  return ((p?.name || '?').trim()[0] || '?').toUpperCase();
}
export const WAKE_LABEL: Record<Wake, string> = { adb: 'Wake with ADB', wol: 'Wake-on-LAN', harmony: 'Harmony', none: '' };
