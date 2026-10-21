/** v224 Settings > Rooms (Option A): /api/rooms (reads signed in; writes the signed-in owner). */
import { apiFetch } from '@/apiClient';

export type Wake = 'adb' | 'wol' | 'harmony' | 'none';
export type RoomPlayer = {
  id: string; name: string; product: string; platform: string; machine_id: string; host: string;
  port: number | null; mac: string; harmony_activity: string; paired: boolean; online: boolean; wake: Wake;
};
/** v231 (Reviewer): a signed-in non-owner gets only id, name, default and online (no harmony_hub or player). */
export type Room = { id: string; name: string; default: boolean; online?: boolean; harmony_hub?: string; player?: RoomPlayer | null };
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
export const createRoom = (name: string, player: Partial<FoundPlayer>) => call<RoomsView>(ROOMS_URL, json('POST', { name, player }), okView);
export type RoomPatch = Partial<{ name: string; player: Partial<FoundPlayer>; wake: Wake; host: string; port: number | string; mac: string;
  harmony_hub: string; harmony_activity: string; default: boolean }>;
export const updateRoom = (id: string, patch: RoomPatch) => call<RoomsView>(`${ROOMS_URL}/${encodeURIComponent(id)}`, json('PUT', patch), okView);
export const removeRoom = (id: string) => call<RoomsView>(`${ROOMS_URL}/${encodeURIComponent(id)}`, json('DELETE'), okView);

/** A player's two- or three-letter badge, as the A mocks: ATV for Android TV, tv for Apple TV, else the initial. */
export function badge(p: { product?: string; platform?: string; name?: string } | null | undefined): string {
  const s = (p?.product || `${p?.platform || ''} ${p?.name || ''}`).toLowerCase();   // the Plex product wins
  if (s.includes('android') || s.includes('shield')) return 'ATV';
  if (s.includes('apple') || s.includes('tvos')) return 'tv';
  return ((p?.name || '?').trim()[0] || '?').toUpperCase();
}
export const WAKE_LABEL: Record<Wake, string> = { adb: 'Wake with ADB', wol: 'Wake-on-LAN', harmony: 'Harmony', none: '' };
