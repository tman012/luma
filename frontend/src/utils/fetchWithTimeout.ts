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
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { signal: controller.signal }).finally(() => clearTimeout(timer));
}
