import { useState, useEffect, useCallback } from 'react';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { apiFetch } from '../apiClient';

type PushState = 'unsupported' | 'prompt' | 'subscribed' | 'denied' | 'loading';

const DISMISS_KEY = 'push_prompt_dismissed';
const SUBSCRIBED_KEY = 'push_subscribed';

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

/** Mobile home banner stays compact-only (prod). Settings > Notifications passes
 * anyLayout so Enable Notifications works on kiosk / desktop PWAs too. */
export function usePushSubscription(opts?: { anyLayout?: boolean }) {
  const anyLayout = !!opts?.anyLayout;
  const { mode } = useLayoutMode();
  const [state, setState] = useState<PushState>('unsupported');
  const [dismissed, setDismissed] = useState(() =>
    localStorage.getItem(DISMISS_KEY) === '1'
  );

  useEffect(() => {
    // Guards against the async VAPID check resolving after this effect run is
    // superseded (e.g. layout mode flips on rotate/resize) or the hook unmounts.
    let cancelled = false;

    // Home banner: mobile only. Settings subscribe: any layout.
    if (!anyLayout && mode !== 'compact') {
      setState('unsupported');
      return;
    }

    // Check browser support — PushManager is the essential API
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      setState('unsupported');
      return;
    }

    // Check current permission (Notification may not exist on all iOS versions
    // in standalone mode before first permission request)
    try {
      if (typeof Notification !== 'undefined' && Notification.permission === 'denied') {
        setState('denied');
        return;
      }
    } catch {
      // Notification API not available — still allow subscribe attempt
    }

    // If we previously completed subscription, start as 'subscribed' to
    // avoid flashing the banner while the async VAPID check runs.
    if (localStorage.getItem(SUBSCRIBED_KEY) === '1') {
      setState('subscribed');
    } else {
      // Show prompt immediately — don't wait for SW ready (it may hang on
      // first PWA launch while the SW is still installing). The subscribe()
      // function will await SW ready when the user taps Enable.
      setState('prompt');
    }

    // Async verification: confirm subscription is still valid with correct
    // VAPID key. If the key changed (server regenerated), unsubscribe the
    // stale sub so the user re-subscribes with the new key.
    (async () => {
      try {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (cancelled) return;
        if (!sub) {
          // No subscription exists — clear persisted flag, show prompt
          localStorage.removeItem(SUBSCRIBED_KEY);
          setState('prompt');
          return;
        }

        // Fetch current VAPID key from server
        const res = await apiFetch('/api/push/vapid-key');
        const { publicKey } = await res.json();
        if (cancelled || !publicKey) return;

        // Compare subscription's applicationServerKey with current server key
        const subKey = sub.options?.applicationServerKey;
        const serverKeyBytes = urlBase64ToUint8Array(publicKey);
        let keysMatch = false;
        if (subKey) {
          const subKeyBytes = new Uint8Array(subKey);
          keysMatch = subKeyBytes.length === serverKeyBytes.length &&
            subKeyBytes.every((b, i) => b === serverKeyBytes[i]);
        }

        if (cancelled) return;
        if (keysMatch) {
          setState('subscribed');
        } else {
          // VAPID key mismatch — unsubscribe stale subscription
          await sub.unsubscribe();
          if (cancelled) return;
          localStorage.removeItem(SUBSCRIBED_KEY);
          setState('prompt');
        }
      } catch {
        // SW not ready or other error — keep current state
      }
    })();

    return () => { cancelled = true; };
  }, [mode, anyLayout]);

  const subscribe = useCallback(async () => {
    setState('loading');
    try {
      // Get VAPID key from server
      const res = await apiFetch('/api/push/vapid-key');
      const { publicKey } = await res.json();

      if (!publicKey) {
        setState('prompt');
        return;
      }

      const reg = await navigator.serviceWorker.ready;

      // Unsubscribe any existing stale subscription first
      const existing = await reg.pushManager.getSubscription();
      if (existing) await existing.unsubscribe();

      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });

      // Send subscription to server
      await apiFetch('/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sub.toJSON()),
      });

      localStorage.setItem(SUBSCRIBED_KEY, '1');
      setState('subscribed');
    } catch (err) {
      console.error('Push subscription failed:', err);
      try {
        if (typeof Notification !== 'undefined' && Notification.permission === 'denied') {
          setState('denied');
          return;
        }
      } catch {}
      setState('prompt');
    }
  }, []);

  const dismiss = useCallback(() => {
    localStorage.setItem(DISMISS_KEY, '1');
    setDismissed(true);
  }, []);

  const showPrompt = state === 'prompt' && !dismissed;

  return { state, subscribe, dismiss, showPrompt };
}
