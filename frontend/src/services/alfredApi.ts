/**
 * Alfred Bridge API Client
 *
 * Sends complex voice commands to the Alfred agent via the backend proxy.
 * Alfred returns structured JSON responses that Luma can render and act on.
 */

import type { AlfredResponse } from '@/types/voice';
import { apiFetch } from '../apiClient';

const ALFRED_TIMEOUT = 90_000; // 90s — Alfred may call Claude AI fallback

export class AlfredApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = 'AlfredApiError';
  }
}

/**
 * Send a natural-language command to Alfred via the backend proxy.
 *
 * @param transcript  The user's spoken/typed command.
 * @param context     Optional context (e.g. current page, active modal).
 * @returns           Structured response from Alfred.
 */
export async function sendToAlfred(
  transcript: string,
  context?: Record<string, unknown>,
  history?: { role: string; content: string }[],
): Promise<AlfredResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ALFRED_TIMEOUT);

  try {
    const resp = await apiFetch('/api/alfred/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript, context, history }),
      signal: controller.signal,
    });

    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new AlfredApiError(
        body.error || `Alfred request failed (${resp.status})`,
        resp.status,
      );
    }

    return await resp.json();
  } catch (err) {
    if (err instanceof AlfredApiError) throw err;
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new AlfredApiError('Alfred took too long to respond.', 408);
    }
    throw new AlfredApiError(
      err instanceof Error ? err.message : 'Failed to reach Alfred.',
      0,
    );
  } finally {
    clearTimeout(timer);
  }
}
