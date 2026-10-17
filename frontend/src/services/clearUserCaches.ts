import { _resetModalPrefetch } from '@/services/modalPrefetch';
import { _resetModalColors } from '@/services/modalColors';

/**
 * On sign-out or a switch to another account: forget everything kept for the
 * previous user, both in memory (prefetched details, colors) and in the
 * service worker's caches.
 */
export function clearUserCaches(): void {
  _resetModalPrefetch();
  _resetModalColors();
  try {
    navigator.serviceWorker?.controller?.postMessage('CLEAR_ALL_CACHES');
  } catch {
    // no service worker: nothing persistent to clear
  }
}
