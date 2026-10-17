/**
 * SettingsContext — shared state for the Settings page.
 *
 * Fetches GET /api/settings + GET /api/settings/schema on mount.
 * Provides getByPath / setByPath for dot-notation access,
 * saveCard for persisting per-card changes, and testConnection.
 */

import {
  createContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  type ReactNode,
} from 'react';
import {
  fetchSettings,
  fetchSchema,
  saveSettings,
  testConnection as apiTestConnection,
  isMasked,
} from '@/services/settingsApi';

// ── Toast ────────────────────────────────────────────────────────

export type ToastType = 'success' | 'error' | 'info';

export interface Toast {
  id: number;
  message: string;
  type: ToastType;
}

// ── Context shape ────────────────────────────────────────────────

export interface SettingsContextValue {
  settings: Record<string, unknown>;
  savedSettings: Record<string, unknown>;
  schema: Record<string, unknown>;
  loading: boolean;
  getByPath: (path: string) => unknown;
  setByPath: (path: string, value: unknown) => void;
  saveCard: (paths: string[]) => Promise<boolean>;
  resetCard: (paths: string[]) => void;
  isCardDirty: (paths: string[]) => boolean;
  /** The last save of these paths needs the owner signed in with a session. */
  needsSignIn: (paths: string[]) => boolean;
  testConnection: (
    service: string,
    body?: Record<string, unknown>,
  ) => Promise<{ success: boolean; message: string }>;
  reload: () => Promise<void>;
  toasts: Toast[];
  showToast: (message: string, type?: ToastType) => void;
  dismissToast: (id: number) => void;
}

export const SettingsContext = createContext<SettingsContextValue | null>(null);

// ── Dot-path helpers ─────────────────────────────────────────────

function _getByPath(obj: Record<string, unknown>, path: string): unknown {
  const parts = path.split('.');
  let node: unknown = obj;
  for (const part of parts) {
    if (node == null || typeof node !== 'object') return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return node;
}

function _setByPath(
  obj: Record<string, unknown>,
  path: string,
  value: unknown,
): Record<string, unknown> {
  const clone = structuredClone(obj);
  const parts = path.split('.');
  let node: Record<string, unknown> = clone;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i]!;
    if (!(key in node) || typeof node[key] !== 'object' || node[key] === null) {
      node[key] = {};
    }
    node = node[key] as Record<string, unknown>;
  }
  node[parts[parts.length - 1]!] = value;
  return clone;
}

// ── Provider ─────────────────────────────────────────────────────

let toastCounter = 0;

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [savedSettings, setSavedSettings] = useState<Record<string, unknown>>({});
  const [schema, setSchema] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);
  const [toasts, setToasts] = useState<Toast[]>([]);
  // Paths whose last save was refused until the owner signs in.
  const [signInPaths, setSignInPaths] = useState<string[]>([]);
  const toastTimers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const showToast = useCallback((message: string, type: ToastType = 'info') => {
    const id = ++toastCounter;
    setToasts((prev) => [...prev, { id, message, type }]);
    const timer = setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      toastTimers.current.delete(id);
    }, 4000);
    toastTimers.current.set(id, timer);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = toastTimers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      toastTimers.current.delete(id);
    }
  }, []);

  const reload = useCallback(async () => {
    try {
      const [s, sc] = await Promise.all([fetchSettings(), fetchSchema()]);
      setSettings(s);
      setSavedSettings(structuredClone(s));
      setSchema(sc);
    } catch (e) {
      showToast(`Failed to load settings: ${(e as Error).message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    reload();
  }, [reload]);

  const getByPath = useCallback(
    (path: string) => _getByPath(settings, path),
    [settings],
  );

  const setByPathFn = useCallback((path: string, value: unknown) => {
    setSettings((prev) => _setByPath(prev, path, value));
    // A new edit is a new change: Save asks the server again.
    setSignInPaths((prev) => (prev.includes(path) ? [] : prev));
  }, []);

  const saveCard = useCallback(
    async (paths: string[]): Promise<boolean> => {
      const changes: Record<string, unknown> = {};
      for (const path of paths) {
        const current = _getByPath(settings, path);
        const saved = _getByPath(savedSettings, path);
        // Skip masked passwords (they haven't been changed)
        if (isMasked(current)) continue;
        // Only send changed values
        if (JSON.stringify(current) !== JSON.stringify(saved)) {
          changes[path] = current;
        }
      }
      if (Object.keys(changes).length === 0) {
        showToast('No changes to save', 'info');
        return true;
      }
      try {
        // Build nested object for PUT
        const nested: Record<string, unknown> = {};
        for (const [dotPath, value] of Object.entries(changes)) {
          const parts = dotPath.split('.');
          let node: Record<string, unknown> = nested;
          for (let i = 0; i < parts.length - 1; i++) {
            const key = parts[i]!;
            if (!node[key] || typeof node[key] !== 'object') {
              node[key] = {};
            }
            node = node[key] as Record<string, unknown>;
          }
          node[parts[parts.length - 1]!] = value;
        }
        const result = await saveSettings(nested);
        if (result.signIn) {
          setSignInPaths(paths);
          return false;
        }
        setSignInPaths([]);
        if (result.success) {
          if (result.settings) {
            setSettings(result.settings);
            setSavedSettings(structuredClone(result.settings));
          } else {
            // Update savedSettings to match current for changed paths
            setSavedSettings((prev) => {
              let updated = structuredClone(prev);
              for (const [dotPath, value] of Object.entries(changes)) {
                updated = _setByPath(updated, dotPath, value);
              }
              return updated;
            });
          }
          showToast('Settings saved', 'success');
          return true;
        } else {
          showToast(result.errors?.join(', ') ?? 'Save failed', 'error');
          return false;
        }
      } catch (e) {
        showToast(`Save failed: ${(e as Error).message}`, 'error');
        return false;
      }
    },
    [settings, savedSettings, showToast],
  );

  const resetCard = useCallback(
    (paths: string[]) => {
      setSignInPaths((prev) => (paths.some((p) => prev.includes(p)) ? [] : prev));
      setSettings((prev) => {
        let updated = structuredClone(prev);
        for (const path of paths) {
          const savedValue = _getByPath(savedSettings, path);
          updated = _setByPath(updated, path, structuredClone(savedValue));
        }
        return updated;
      });
    },
    [savedSettings],
  );

  const isCardDirty = useCallback(
    (paths: string[]) => {
      for (const path of paths) {
        const current = _getByPath(settings, path);
        const saved = _getByPath(savedSettings, path);
        if (isMasked(current)) continue;
        if (JSON.stringify(current) !== JSON.stringify(saved)) return true;
      }
      return false;
    },
    [settings, savedSettings],
  );

  const needsSignIn = useCallback(
    (paths: string[]) => paths.some((p) => signInPaths.includes(p)),
    [signInPaths],
  );

  const testConnectionFn = useCallback(
    async (
      service: string,
      body?: Record<string, unknown>,
    ): Promise<{ success: boolean; message: string }> => {
      try {
        return await apiTestConnection(service, body);
      } catch (e) {
        return { success: false, message: (e as Error).message };
      }
    },
    [],
  );

  return (
    <SettingsContext.Provider
      value={{
        settings,
        savedSettings,
        schema,
        loading,
        getByPath,
        setByPath: setByPathFn,
        saveCard,
        resetCard,
        isCardDirty,
        needsSignIn,
        testConnection: testConnectionFn,
        reload,
        toasts,
        showToast,
        dismissToast,
      }}
    >
      {children}
    </SettingsContext.Provider>
  );
}
