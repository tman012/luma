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

import { createContext, useContext, type ReactNode } from 'react';
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
