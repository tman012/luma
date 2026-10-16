/**
 * Runtime URL helpers that respect the URL base configuration.
 * 
 * The server injects window.__LUMA_URL_BASE__ at runtime via a meta tag.
 * These helpers ensure all API and static asset URLs include the base prefix.
 */

/**
 * Get the URL base from the runtime injection.
 * Returns empty string if no base is configured.
 */
export function getUrlBase(): string {
  if (typeof window !== 'undefined' && window.__LUMA_URL_BASE__) {
    return window.__LUMA_URL_BASE__;
  }
  return '';
}

/**
 * Construct an API URL with the runtime URL base.
 * @param path - API path (should start with '/')
 * @returns Full API URL including base
 */
export function apiUrl(path: string): string {
  const base = getUrlBase();
  // Ensure path starts with /
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${cleanPath}`;
}

/**
 * Construct an asset URL with the runtime URL base.
 * @param path - Asset path (should start with '/')
 * @returns Full asset URL including base
 */
export function assetUrl(path: string): string {
  const base = getUrlBase();
  // Ensure path starts with /
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${cleanPath}`;
}

// Type declaration for the runtime global
declare global {
  interface Window {
    __LUMA_URL_BASE__?: string;
  }
}
