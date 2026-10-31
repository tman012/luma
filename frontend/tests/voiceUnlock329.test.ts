import { describe, expect, it, vi } from 'vitest';

describe('v329 the audio unlock never runs inside a sheet-opening tap', () => {
  it('a .luma-pressable click leaves it armed; the next other click unlocks', async () => {
    const ctor = vi.fn(function (this: Record<string, unknown>) {
      Object.assign(this, { state: 'running', createBuffer: () => ({}), createBufferSource: () => ({ connect() {}, start() {} }), destination: {} });
    });
    vi.stubGlobal('AudioContext', ctor);
    vi.resetModules();
    await import('@/hooks/useVoiceAssistant');
    const card = document.createElement('div'); card.className = 'luma-pressable';
    const inner = document.createElement('span'); card.appendChild(inner); document.body.appendChild(card);
    inner.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(ctor).not.toHaveBeenCalled();
    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(ctor).toHaveBeenCalledTimes(1);
    card.remove(); vi.unstubAllGlobals();
  });
});
