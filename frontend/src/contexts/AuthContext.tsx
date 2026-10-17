/**
 * AuthContext — manages the current logged-in user across the app.
 *
 * Auth is session-cookie based. The cookie is set/cleared by the Flask backend.
 * On mount we call /api/auth/me to restore state from the existing cookie.
 */
import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  ReactNode,
} from 'react';
import { apiFetch } from '../apiClient';
import { clearUserCaches } from '@/services/clearUserCaches';

export interface UserInfo {
  id: number;
  name: string;
  email?: string | null;
  is_admin: boolean;
}

export interface UserSettings {
  plex_url?: string;
  plex_token?: string;
  use_shared_server?: boolean;
  preferred_room_id?: string;
  preferred_device_id?: string;
  preferred_service_id?: string;
  _shared_plex_url?: string;
  // Per-user Arr stack
  radarr_url?: string;
  radarr_api_key?: string;
  radarr_quality_profile_id?: number;
  sonarr_url?: string;
  sonarr_api_key?: string;
  // Per-user rooms
  rooms?: Array<Record<string, unknown>>;
}

interface AuthState {
  user: UserInfo | null;
  userSettings: UserSettings;
  loading: boolean;
  authenticated: boolean;
  needsSetup: boolean;
  plexAccessStatus: string | null; // 'pending' | 'approved' | 'denied' | null
  /** Fresh install with no accounts: only the Start flow is available. */
  needsStart: boolean;
  /** Owner was just created by the Start flow and hasn't tapped Done yet. */
  startInProgress: boolean;
  /** Single-account install: sign in with the PIN keypad instead of the login form. */
  pinUnlock: boolean;
  /** Signed in implicitly because access is open (no sign-in needed). */
  implicit: boolean;
  authRequired: 'disabled_local' | 'enabled' | null;
  /** Account requests are accepted from this device. */
  signupOpen: boolean;
}

interface AuthContextValue extends AuthState {
  login: (identifier: string, pin: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  saveUserSettings: (updates: Partial<UserSettings>) => Promise<{ success: boolean; error?: string }>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const CACHED_FOR_KEY = 'luma:cachesFor';

function readCachedFor(): number | null {
  try {
    const v = sessionStorage.getItem(CACHED_FOR_KEY);
    return v ? Number(v) : null;
  } catch {
    return null;
  }
}

function writeCachedFor(id: number | null): void {
  try {
    if (id == null) sessionStorage.removeItem(CACHED_FOR_KEY);
    else sessionStorage.setItem(CACHED_FOR_KEY, String(id));
  } catch {
    // storage unavailable: the in-memory check still works this session
  }
}

const NO_START = { needsStart: false, startInProgress: false, pinUnlock: false, implicit: false, authRequired: null, signupOpen: false } as const;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    userSettings: {},
    loading: true,
    authenticated: false,
    needsSetup: false,
    plexAccessStatus: null,
    ...NO_START,
  });
  // The account the caches were filled for (kept across reloads in this
  // tab); a different one clears them.
  const cachedFor = useRef<number | null>(readCachedFor());
  const noteUser = useCallback((id: number | null | undefined) => {
    if (id == null) return;
    if (cachedFor.current != null && cachedFor.current !== id) clearUserCaches();
    cachedFor.current = id;
    writeCachedFor(id);
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const res = await apiFetch('/api/auth/me');
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.authenticated) {
        noteUser(data.user?.id);
        setState({
          user: data.user,
          userSettings: data.user_settings || {},
          loading: false,
          authenticated: true,
          needsSetup: !!data.needs_setup,
          plexAccessStatus: data.plex_access_status || null,
          needsStart: false,
          startInProgress: !!data.start_in_progress,
          pinUnlock: false,
          implicit: !!data.implicit,
          authRequired: data.auth_required || null,
          signupOpen: false,
        });
        return;
      }
      setState({
        user: null, userSettings: {}, loading: false, authenticated: false, needsSetup: false, plexAccessStatus: null,
        ...NO_START, needsStart: !!data.needs_start, pinUnlock: !!data.pin_unlock, signupOpen: !!data.signup,
      });
      return;
    } catch {
      // network error — stay unauthenticated
    }
    setState({ user: null, userSettings: {}, loading: false, authenticated: false, needsSetup: false, plexAccessStatus: null, ...NO_START });
  }, [noteUser]);

  // Restore session on mount
  useEffect(() => {
    refreshUser();
  }, [refreshUser]);

  const login = useCallback(async (identifier: string, pin: string) => {
    try {
      const res = await apiFetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: identifier, name: identifier, pin }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        noteUser(data.user?.id);
        // Set initial auth state, then refresh to get full settings + needsSetup
        setState({
          user: data.user,
          userSettings: {},
          loading: false,
          authenticated: true,
          needsSetup: false,
          plexAccessStatus: null,
          ...NO_START,
        });
        await refreshUser();
        return { success: true };
      }
      return { success: false, error: data.error || 'Login failed' };
    } catch (e) {
      return { success: false, error: 'Network error' };
    }
  }, [refreshUser, noteUser]);

  const logout = useCallback(async () => {
    try {
      await apiFetch('/api/auth/logout', { method: 'POST' });
    } catch {}
    clearUserCaches();
    cachedFor.current = null;
    writeCachedFor(null);
    setState({ user: null, userSettings: {}, loading: false, authenticated: false, needsSetup: false, plexAccessStatus: null, ...NO_START });
  }, []);

  const saveUserSettings = useCallback(async (updates: Partial<UserSettings>) => {
    try {
      const res = await apiFetch('/api/user/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setState(prev => ({
          ...prev,
          userSettings: { ...prev.userSettings, ...updates },
        }));
        return { success: true };
      }
      return { success: false, error: data.error };
    } catch (e) {
      return { success: false, error: 'Network error' };
    }
  }, []);

  return (
    <AuthContext.Provider value={{ ...state, login, logout, refreshUser, saveUserSettings }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
