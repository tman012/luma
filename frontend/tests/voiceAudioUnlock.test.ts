import { afterEach, describe, expect, it, vi } from 'vitest';

// CI failed on 12 "AudioContext is not defined" errors: the first click in any
// test ran the voice unlock listener, which assumed Web Audio exists.
describe('voice audio unlock', () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it('a click without Web Audio does not throw', async () => {
    vi.stubGlobal('AudioContext', undefined);
    const errors: unknown[] = [];
    const onErr = (e: ErrorEvent) => { errors.push(e.error); e.preventDefault(); };
    window.addEventListener('error', onErr);
    const mod = await import('@/hooks/useVoiceAssistant');
    expect(() => mod.primeVoiceAudio()).not.toThrow();
    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    window.removeEventListener('error', onErr);
    expect(errors).toEqual([]);
  });

  it('primes a real AudioContext when there is one', async () => {
    const resume = vi.fn(() => Promise.resolve());
    const start = vi.fn();
    class FakeCtx {
      state = 'suspended';
      destination = {};
      resume = resume;
      createBuffer() { return {}; }
      createBufferSource() { return { connect: vi.fn(), start }; }
    }
    vi.stubGlobal('AudioContext', FakeCtx);
    vi.resetModules();
    const mod = await import('@/hooks/useVoiceAssistant');
    mod.primeVoiceAudio();
    expect(resume).toHaveBeenCalled();
    expect(start).toHaveBeenCalled();
  });
});
