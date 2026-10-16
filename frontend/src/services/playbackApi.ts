// ---------------------------------------------------------------------------
// Playback API service — wraps backend Kodi playback endpoints
// ---------------------------------------------------------------------------

export interface Service {
  id: string;
  type: 'kodi' | 'plex';
  kodi_host?: string;
  kodi_port?: number;
  kodi_username?: string;
  kodi_password?: string;
}

export interface Device {
  id: string;
  name: string;
  platform: 'android_tv' | 'chromecast_gtv' | 'network';
  host: string;
  connectPort: number;
  pairPort: number | null;
  paired: boolean;
  macAddress?: string;
  services: Service[];
}

export interface Room {
  id: string;
  name: string;
  devices: Device[];
}

export interface PlaybackTarget {
  roomId: string;
  deviceId: string;
  serviceId: string;
}

export interface ResumeInfo {
  has_resume_point: boolean;
  offset_ms: number;
  offset_seconds: number;
  time_string: string;
  rating_key: string;
}

export interface PlexSeason {
  ratingKey: number;
  title: string;
  index: number;  // season number
  thumbPath: string | null;
  leafCount: number;
  viewedLeafCount: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function roomIcon(name: string): string {
  const lower = name.toLowerCase();
  if (lower.includes('bed')) return 'bed';
  if (lower.includes('theater') || lower.includes('theatre')) return 'film';
  if (lower.includes('office')) return 'laptop';
  return 'tv';
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function fetchRooms(): Promise<{
  rooms: Room[];
  defaultPlaybackTarget: PlaybackTarget;
}> {
  const resp = await fetch('/api/settings/rooms');
  if (!resp.ok) return { rooms: [], defaultPlaybackTarget: { roomId: '', deviceId: '', serviceId: '' } };
  const data = await resp.json();
  return {
    rooms: (data.rooms ?? []) as Room[],
    defaultPlaybackTarget: data.defaultPlaybackTarget ?? { roomId: '', deviceId: '', serviceId: '' },
  };
}

export async function getResumeInfo(ratingKey: number): Promise<ResumeInfo> {
  const resp = await fetch(`/api/plex/resume_info/${ratingKey}`);
  if (!resp.ok) {
    return {
      has_resume_point: false,
      offset_ms: 0,
      offset_seconds: 0,
      time_string: '0:00',
      rating_key: String(ratingKey),
    };
  }
  return resp.json();
}

export async function startPlayback(
  ratingKey: number,
  clientIp: string,
  roomId: string,
  resumeOffset: number = 0,
  playQueueId?: number,
  deviceId?: string,
  serviceId?: string,
): Promise<{ success: boolean; message?: string }> {
  const body: Record<string, unknown> = {
    rating_key: ratingKey,
    client_ip: clientIp,
    room_id: roomId,
    resume_offset: resumeOffset,
  };
  if (playQueueId) body.play_queue_id = playQueueId;
  if (deviceId) body.device_id = deviceId;
  if (serviceId) body.service_id = serviceId;

  const resp = await fetch('/api/player/play', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return resp.json();
}

export async function fetchPlexSeasons(
  showRatingKey: number,
): Promise<PlexSeason[]> {
  const resp = await fetch(`/api/plex/tv/${showRatingKey}/seasons`);
  if (!resp.ok) return [];
  const data = await resp.json();
  return (data.seasons ?? []) as PlexSeason[];
}

export async function createPlayQueue(
  ratingKey: number,
  shuffle: boolean = true,
): Promise<{ success: boolean; play_queue_id?: number; error?: string }> {
  const resp = await fetch('/api/plex/playqueue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rating_key: ratingKey, shuffle }),
  });
  return resp.json();
}

export { roomIcon };
