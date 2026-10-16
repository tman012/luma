/**
 * API client with automatic URL base handling.
 * 
 * This wraps the native fetch to automatically prepend the runtime URL base
 * to all API and static asset requests.
 */

import { apiUrl } from './urlHelpers';

/**
 * Fetch wrapper that automatically prepends the URL base to relative URLs.
 * 
 * For API calls (/api/*), this ensures the correct base is used even when
 * the app is hosted at a sub-path (e.g. /luma/api/settings).
 * 
 * @param input - URL or Request object
 * @param init - Fetch options
 * @returns Promise<Response>
 */
export async function apiFetch(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<Response> {
  // If it's a string URL starting with /, prepend the base
  if (typeof input === 'string' && input.startsWith('/')) {
    input = apiUrl(input);
  }
  
  return fetch(input, init);
}

// Re-export for convenience
export { apiUrl, assetUrl, getUrlBase } from './urlHelpers';
