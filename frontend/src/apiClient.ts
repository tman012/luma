/**
 * API client with automatic URL base handling.
 * 
 * All API calls should use apiFetch instead of native fetch to ensure
 * the runtime URL base is correctly applied.
 */

import { apiUrl } from './urlHelpers';

/**
 * Fetch wrapper that automatically prepends the URL base to relative URLs.
 * 
 * Use this for all API calls instead of native fetch.
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
  // If it's a Request object with a relative URL, create a new Request with the base
  else if (input instanceof Request && input.url.startsWith('/')) {
    input = new Request(apiUrl(input.url), input);
  }
  
  return fetch(input, init);
}

// Re-export for convenience
export { apiUrl, assetUrl, getUrlBase } from './urlHelpers';
