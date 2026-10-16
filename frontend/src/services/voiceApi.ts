/** Voice Assistant API service */

interface VoiceAction {
  tool: string;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
}

interface VoiceCommandResponse {
  reply: string;
  actions_taken: VoiceAction[];
  error?: string;
}

export async function sendVoiceCommand(
  transcript: string,
  history: { role: string; content: string }[],
): Promise<VoiceCommandResponse> {
  const resp = await fetch('/api/voice/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transcript, history }),
  });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `Voice command failed (${resp.status})`);
  }

  return resp.json();
}
