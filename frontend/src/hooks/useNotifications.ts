import { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '@/services/api';

export interface NotificationItem {
  tmdbTitle: string;
  tmdbYear: string;
  mediaType: string;
  completedTimestamp: number;
  libraryName: string;
  posterPath: string | null;
  tmdbId: number | null;
  seasonNumber: number | null;
  episodeNumber: number | null;
}

export interface DownloadItem {
  queueId: number | null;
  name: string;           // raw torrent name — stable identity (titles can collide)
  hash: string;           // qBittorrent info-hash (for remove / delete+files)
  title: string;
  year: number;
  tmdbId: number | null;
  posterPath: string | null;
  progress: number;       // 0..1
  sizeTotal: number;
  sizeRemaining: number;
  speed: number;          // bytes/sec (download speed; 0 when copying/importing)
  eta: string | null;     // "1h 23m" or null
  status: string;
  tracker: string | null;
}

export interface SessionData {
  ratingKey: string;
  title: string;
  year: string;
  type: string;           // 'movie' | 'episode'
  state: string;          // 'playing' | 'paused'
  viewOffset: number;     // ms
  duration: number;       // ms
  thumb: string;
  art: string;
  contentRating: string;
  userName?: string;
  grandparentTitle?: string;
  parentIndex?: string;
  index?: string;
  grandparentThumb?: string;
}

export interface ActivityData {
  appName: string;        // 'YouTube', 'Netflix', 'Plex', 'Kodi', etc.
  state: string;          // 'playing' | 'paused'
  title: string | null;   // Media title if available
  year?: string;          // Release year (e.g. '2019')
  duration: number;       // ms
  position: number;       // ms
  progressPercent: number; // 0-100
  thumb?: string | null;  // Poster image URL (e.g. TMDb proxy)
  art?: string | null;    // Backdrop image URL (e.g. Plex or TMDb proxy)
  deviceId?: string;      // Device that is playing (for targeted remote commands)
  mediaType?: string;     // optional 'live' / etc. when backend sends it (v290 Live TV art)
}

export interface Room {
  roomId: string;
  roomName: string;
  deviceCount: number;
  session: SessionData | null;
  activity: ActivityData | null;
}

interface UnseenResponse {
  count: number;
  items: Array<{
    tmdb_title: string;
    tmdb_year: string;
    media_type: string;
    completed_timestamp: number;
    library_name: string;
    poster_path: string | null;
    tmdb_id: number | null;
    season_number: number | null;
    episode_number: number | null;
  }>;
}

interface QueueResponse {
  downloads: Array<{
    queue_id: number | null;
    name?: string;
    hash?: string;
    title: string;
    year: number;
    tmdb_id: number | null;
    poster_path: string | null;
    progress: number;
    size_total: number;
    size_remaining: number;
    speed?: number;
    eta: string | null;
    status: string;
    tracker: string | null;
  }>;
}

function mapItem(raw: UnseenResponse['items'][number]): NotificationItem {
  return {
    tmdbTitle: raw.tmdb_title,
    tmdbYear: raw.tmdb_year,
    mediaType: raw.media_type,
    completedTimestamp: raw.completed_timestamp,
    libraryName: raw.library_name,
    posterPath: raw.poster_path,
    tmdbId: raw.tmdb_id,
    seasonNumber: raw.season_number ?? null,
    episodeNumber: raw.episode_number ?? null,
  };
}

function mapDownload(raw: QueueResponse['downloads'][number]): DownloadItem {
  return {
    queueId: raw.queue_id,
    name: raw.name || raw.title,
    hash: (raw.hash || '').toLowerCase(),
    title: raw.title,
    year: raw.year,
    tmdbId: raw.tmdb_id,
    posterPath: raw.poster_path,
    progress: raw.progress,
    sizeTotal: raw.size_total,
    sizeRemaining: raw.size_remaining,
    speed: raw.speed ?? 0,
    eta: raw.eta,
    status: raw.status,
    tracker: raw.tracker ?? null,
  };
}

/** Poll interval when downloads are active */
const DOWNLOAD_POLL_ACTIVE = 3_000;
/** Poll interval when no downloads */
const DOWNLOAD_POLL_IDLE = 5_000;
/** Poll interval for now-playing sessions */
const SESSIONS_POLL = 3_000;

export function useNotifications(pollInterval = 60_000) {
  const [count, setCount] = useState(0);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [downloads, setDownloads] = useState<DownloadItem[]>([]);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [roomsLoading, setRoomsLoading] = useState(true);
  const [unmatchedSessions, setUnmatchedSessions] = useState<SessionData[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const dlTimerRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const sessTimerRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const dlActiveRef = useRef(false);
  const notifiedRef = useRef<Set<string>>(new Set());

  const fetchUnseen = useCallback(async () => {
    // Don't poll while the tab/kiosk is hidden (e.g. screensaver) — no user benefit.
    if (typeof document !== 'undefined' && document.hidden) return;
    try {
      const data = await apiFetch<UnseenResponse>('/api/notifications/unseen');
      setCount(data.count);
      setItems(data.items.map(mapItem));
    } catch {
      // Silently ignore — keep last known state
    }
  }, []);

  const fetchQueue = useCallback(async () => {
    if (typeof document !== 'undefined' && document.hidden) return;
    try {
      // Unified queue: every active qBittorrent torrent (any origin, incl.
      // manual/Sonarr) PLUS post-download copy-into-library progress. Replaces
      // the Radarr-only /api/radarr/queue so the bell + downloads sheet show
      // both download AND copy progress consistently with the Activity feed.
      const data = await apiFetch<QueueResponse>('/api/downloads/queue');
      const mapped = data.downloads.map(mapDownload);

      setDownloads(prev => {
        // Key by the raw torrent name (stable & unique), not the display title —
        // two concurrent downloads sharing a title would otherwise overwrite each
        // other, misfiring completion notifications and clamping progress.
        const prevMap = new Map(prev.map(d => [d.name, d]));
        const newKeys = new Set(mapped.map(d => d.name));

        // Inject notification instantly when a COPYING item disappears
        // from the downloads list — that means the file copy actually
        // completed.  Importing items can vanish before the copy starts
        // (Radarr clears queue early), so skip those.
        for (const old of prev) {
          if (old.status === 'copying'
              && !newKeys.has(old.name)
              && !notifiedRef.current.has(old.name)) {
            notifiedRef.current.add(old.name);
            setTimeout(() => notifiedRef.current.delete(old.name), 30_000);
            const notif: NotificationItem = {
              tmdbTitle: old.title,
              tmdbYear: old.year > 0 ? String(old.year) : '',
              mediaType: 'movie',
              completedTimestamp: Date.now() / 1000,
              libraryName: 'Movies',
              posterPath: old.posterPath,
              tmdbId: old.tmdbId,
              seasonNumber: null,
              episodeNumber: null,
            };
            setItems(cur_items => [notif, ...cur_items]);
            setCount(c => c + 1);
            fetchUnseen();
          }
        }

        // Enforce monotonic progress: never let progress go backwards
        return mapped.map(d => {
          const old = prevMap.get(d.name);
          if (old && d.status === old.status && d.progress < old.progress) {
            return { ...d, progress: old.progress };
          }
          return d;
        });
      });

      // Adjust poll rate based on active downloads
      const hasActive = mapped.length > 0;
      if (hasActive !== dlActiveRef.current) {
        dlActiveRef.current = hasActive;
        clearInterval(dlTimerRef.current);
        dlTimerRef.current = setInterval(
          fetchQueue,
          hasActive ? DOWNLOAD_POLL_ACTIVE : DOWNLOAD_POLL_IDLE,
        );
      }
    } catch {
      // Silently ignore
    }
  }, [fetchUnseen]);

  useEffect(() => {
    fetchUnseen();
    timerRef.current = setInterval(fetchUnseen, pollInterval);
    return () => clearInterval(timerRef.current);
  }, [fetchUnseen, pollInterval]);

  useEffect(() => {
    fetchQueue();
    dlTimerRef.current = setInterval(fetchQueue, DOWNLOAD_POLL_IDLE);

    // Immediately refresh queue when a grab happens anywhere in the app
    const onGrabbed = () => {
      // Switch to active polling immediately, then fetch
      dlActiveRef.current = true;
      clearInterval(dlTimerRef.current);
      dlTimerRef.current = setInterval(fetchQueue, DOWNLOAD_POLL_ACTIVE);
      fetchQueue();
    };
    window.addEventListener('download-grabbed', onGrabbed);

    return () => {
      clearInterval(dlTimerRef.current);
      window.removeEventListener('download-grabbed', onGrabbed);
    };
  }, [fetchQueue]);

  const fetchRooms = useCallback(async (force = false) => {
    // Skip background polls while hidden; an explicit forced refresh still runs.
    if (!force && typeof document !== 'undefined' && document.hidden) return;
    try {
      const params = force ? `force=1&_t=${Date.now()}` : `_t=${Date.now()}`;
      const data = await apiFetch<{ rooms?: Room[]; unmatched?: SessionData[] }>(`/api/plex/sessions/active?${params}`);
      setRooms(data.rooms ?? []);
      setUnmatchedSessions(data.unmatched ?? []);
    } catch {
      // Silently ignore — keep last known state
    } finally {
      setRoomsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRooms();
    sessTimerRef.current = setInterval(fetchRooms, SESSIONS_POLL);

    // Listen for playback-started events — optimistically update room, then force-refresh
    const onPlaybackStarted = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.roomId && detail?.ratingKey) {
        // Optimistic: inject a minimal session into the matching room immediately
        setRooms(prev => prev.map(room => {
          if (room.roomId !== detail.roomId || room.session) return room;
          return {
            ...room,
            session: {
              ratingKey: detail.ratingKey,
              title: detail.title ?? '',
              year: '',
              type: 'movie',
              state: detail.state ?? 'playing',
              viewOffset: detail.viewOffset ?? 0,
              duration: detail.duration ?? 0,
              thumb: '',
              art: '',
              contentRating: '',
            },
          };
        }));
      }
      // Force-refresh to get full session data (poster, year, etc.)
      fetchRooms(true);
    };
    window.addEventListener('playback-started', onPlaybackStarted);

    return () => {
      clearInterval(sessTimerRef.current);
      window.removeEventListener('playback-started', onPlaybackStarted);
    };
  }, [fetchRooms]);

  // When the kiosk/tab becomes visible again, immediately refresh everything so
  // the paused pollers don't leave stale data on screen.
  useEffect(() => {
    const onVisible = () => {
      if (typeof document !== 'undefined' && !document.hidden) {
        fetchQueue();
        fetchUnseen();
        fetchRooms();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [fetchQueue, fetchUnseen, fetchRooms]);

  const markSeen = useCallback(async () => {
    // Clear badge but keep items visible so user can read them
    setCount(0);
    try {
      await apiFetch('/api/notifications/mark-seen', { method: 'POST' });
    } catch {
      fetchUnseen();
    }
  }, [fetchUnseen]);

  const removeDownload = useCallback(async (queueId: number) => {
    setDownloads(prev => prev.filter(d => d.queueId !== queueId));
    try {
      await apiFetch(`/api/radarr/queue/${queueId}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ removeFromClient: true, blocklist: false }),
      });
    } catch {
      fetchQueue();
    }
  }, [fetchQueue]);

  // Clear items on next poll after mark-seen (items stay visible until dropdown closes)
  const clearItems = useCallback(() => {
    setItems([]);
  }, []);

  return { count, items, downloads, rooms, roomsLoading, unmatchedSessions, markSeen, clearItems, removeDownload, refreshQueue: fetchQueue };
}
