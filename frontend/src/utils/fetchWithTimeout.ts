import { apiFetch } from '../apiClient';
/**
 * fetchWithTimeout — wraps native fetch with an AbortController timeout.
 *
 * Used by rail fetchers and data-loading helpers to prevent slow endpoints
 * from blocking the UI indefinitely.
 */

const DEFAULT_TIMEOUT_MS = 10_000;

export function fetchWithTimeout(
  url: string,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  signal?: AbortSignal,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // Abort too when the caller's signal fires (unmount, reveal cap, refresh).
  const onAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', onAbort, { once: true });
  }
  return apiFetch(url, { signal: controller.signal }).finally(() => {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  });
}
