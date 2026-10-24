/**
 * NotificationsContext — single shared instance of the notifications/queue
 * pollers.
 *
 * `useNotifications` owns three poll loops (queue, sessions, unseen). It used to
 * be called independently by TopBar + every always-mounted sheet/page, so the
 * home screen ran those loops 4-5× over. This provider runs them ONCE and shares
 * the result; consumers use `useNotificationsContext()` instead of the hook.
 *
 * The pollers only run once authenticated — before that a static empty value is
 * provided so consumers rendered on public routes don't poll or crash.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { apiFetch } from '@/services/api';
import type { Room, SessionData } from '@/hooks/useNotifications';
import { useNotifications } from '@/hooks/useNotifications';
import { useAuth } from '@/contexts/AuthContext';

type NotificationsValue = ReturnType<typeof useNotifications>;

const EMPTY_VALUE: NotificationsValue = {
  count: 0,
  items: [],
  downloads: [],
  rooms: [],
  roomsLoading: false,
  unmatchedSessions: [],
  markSeen: async () => {},
  clearItems: () => {},
  removeDownload: async () => {},
  refreshQueue: async () => {},
};

const NotificationsContext = createContext<NotificationsValue>(EMPTY_VALUE);

/** Runs the real pollers — only mounted when authenticated. */
function ActiveNotificationsProvider({ children }: { children: ReactNode }) {
  const value = useNotifications();
  return (
    <NotificationsContext.Provider value={value}>
      {children}
    </NotificationsContext.Provider>
  );
}

export function NotificationsProvider({ children }: { children: ReactNode }) {
  const { authenticated } = useAuth();
  if (!authenticated) {
    return (
      <NotificationsContext.Provider value={EMPTY_VALUE}>
        {children}
      </NotificationsContext.Provider>
    );
  }
  return <ActiveNotificationsProvider>{children}</ActiveNotificationsProvider>;
}

export function useNotificationsContext(): NotificationsValue {
  return useContext(NotificationsContext);
}

/** v265 minimal build: prod's Rooms sheet reads rooms from this context, but the minimal app runs no
 * notification pollers. This provides only prod's sessions poll (GET /api/plex/sessions/active every 3 s,
 * as useNotifications does), mounted while the sheet is open. */
export function RoomsSessionsProvider({ active, children }: { active: boolean; children: ReactNode }) {
  const [rooms, setRooms] = useState<Room[]>([]);
  const [unmatchedSessions, setUnmatched] = useState<SessionData[]>([]);
  const [roomsLoading, setLoading] = useState(true);
  useEffect(() => {
    if (!active) return;
    let live = true;
    const load = async () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      try {
        const d = await apiFetch<{ rooms?: Room[]; unmatched?: SessionData[] }>(`/api/plex/sessions/active?_t=${Date.now()}`);
        if (live) { setRooms(d.rooms ?? []); setUnmatched(d.unmatched ?? []); }
      } catch { /* keep last known state, as prod */ } finally { if (live) setLoading(false); }
    };
    void load();
    const t = setInterval(load, 3_000);
    return () => { live = false; clearInterval(t); };
  }, [active]);
  return (
    <NotificationsContext.Provider value={{ ...EMPTY_VALUE, rooms, roomsLoading, unmatchedSessions }}>
      {children}
    </NotificationsContext.Provider>
  );
}
