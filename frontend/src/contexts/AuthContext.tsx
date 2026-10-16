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
  ReactNode,
} from 'react';

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
}

interface AuthContextValue extends AuthState {
  login: (identifier: string, pin: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  saveUserSettings: (updates: Partial<UserSettings>) => Promise<{ success: boolean; error?: string }>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    userSettings: {},
    loading: true,
    authenticated: false,
    needsSetup: false,
    plexAccessStatus: null,
  });

  const refreshUser = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/me');
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated) {
          setState({
            user: data.user,
            userSettings: data.user_settings || {},
            loading: false,
            authenticated: true,
            needsSetup: !!data.needs_setup,
            plexAccessStatus: data.plex_access_status || null,
          });
          return;
        }
      }
    } catch {
      // network error — stay unauthenticated
    }
    setState({ user: null, userSettings: {}, loading: false, authenticated: false, needsSetup: false, plexAccessStatus: null });
  }, []);

  // Restore session on mount
  useEffect(() => {
    refreshUser();
  }, [refreshUser]);

  const login = useCallback(async (identifier: string, pin: string) => {
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: identifier, name: identifier, pin }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        // Set initial auth state, then refresh to get full settings + needsSetup
        setState({
          user: data.user,
          userSettings: {},
          loading: false,
          authenticated: true,
          needsSetup: false,
          plexAccessStatus: null,
        });
        await refreshUser();
        return { success: true };
      }
      return { success: false, error: data.error || 'Login failed' };
    } catch (e) {
      return { success: false, error: 'Network error' };
    }
  }, [refreshUser]);

  const logout = useCallback(async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch {}
    setState({ user: null, userSettings: {}, loading: false, authenticated: false, needsSetup: false, plexAccessStatus: null });
  }, []);

  const saveUserSettings = useCallback(async (updates: Partial<UserSettings>) => {
    try {
      const res = await fetch('/api/user/settings', {
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
