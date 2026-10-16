import { useEffect, useRef, useCallback, useMemo } from 'react';

/**
 * Always-on wake word detection.
 *
 * Two backends, picked automatically:
 *
 *   1. **Native (preferred)** — when running inside the LumaKiosk Android
 *      APK on a Fire HD / de-Googled device, the activity exposes a
 *      `window.LumaKiosk` bridge backed by a Vosk on-device recognizer.
 *      We simply listen for the `luma-native-wake` CustomEvent that the
 *      APK dispatches and forward it to `onWake`. No mic contention — the
 *      native side releases AudioRecord before firing.
 *
 *   2. **Web SpeechRecognition (fallback)** — desktop Chrome / dev machines
 *      where Google's speech service is available. Same regex-driven match
 *      and auto-restart loop as before.
 *
 * Command recording always uses MediaRecorder + audio-level VAD via
 * `useVoiceAssistant`, regardless of which wake word backend is in use.
 */

interface LumaKioskBridge {
  isNativeWakeWordAvailable?: () => boolean;
  pauseWakeWord?: () => void;
  resumeWakeWord?: () => void;
  setWakeWord?: (word: string) => void;
  getWakeWord?: () => string;
}

function getLumaBridge(): LumaKioskBridge | null {
  if (typeof window === 'undefined') return null;
  const b = (window as unknown as { LumaKiosk?: LumaKioskBridge }).LumaKiosk;
  if (!b || typeof b.isNativeWakeWordAvailable !== 'function') return null;
  try {
    return b.isNativeWakeWordAvailable() ? b : null;
  } catch {
    return null;
  }
}

/** Common phonetic misrecognitions for known wake words */
const MISRECOGNITION_MAP: Record<string, string[]> = {
  alfred: ['all\\s*fred', 'al\\s+fred', 'offered', 'alfrid', 'alferd', 'alford'],
  jarvis: ['jarves', 'jervis', 'service', 'jarvas'],
  nova: ['no\\s+va', 'no\\s+but', 'noah', 'mova', 'noba', 'nowhere', 'no\\s*vah', 'no\\s*vuh', 'nover', 'no\\s*ver', 'novak', 'nova\\.?', 'know\\s*va', 'know\\s*vah', 'now\\s*va'],
  luma: ['looma', 'lumma', 'llama'],
  friday: ['fry day', 'free day'],
  computer: ['compooter'],
};

function buildWakePattern(word: string): RegExp {
  const w = word.trim().toLowerCase();
  // Build alternations: "hey <word>", "okay <word>", "ok <word>", "<word>", + misrecognitions
  const escaped = w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const alts = [
    `hey\\s+${escaped}`,
    `okay\\s+${escaped}`,
    `ok\\s+${escaped}`,
    escaped,
    ...(MISRECOGNITION_MAP[w] || []),
  ];
  return new RegExp(`\\b(${alts.join('|')})\\b`, 'i');
}

export function useWakeWord({
  enabled,
  wakeWord = 'alfred',
  onWake,
}: {
  /** Master switch — must be true for wake word to run (voice_enabled setting) */
  enabled: boolean;
  /** The wake word to listen for */
  wakeWord?: string;
  /** Called when wake word is detected */
  onWake: () => void;
}) {
  const recognitionRef = useRef<any>(null);
  const onWakeRef = useRef(onWake);
  onWakeRef.current = onWake;

  // Track enabled state via ref so onresult can check synchronously
  // (SpeechRecognition can deliver buffered results after abort/disable)
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  // Cooldown to avoid rapid re-triggers
  const lastTriggerRef = useRef(0);
  const COOLDOWN_MS = 1500;

  // Build regex from the configured wake word
  const wakePattern = useMemo(() => buildWakePattern(wakeWord), [wakeWord]);
  const wakePatternRef = useRef(wakePattern);
  wakePatternRef.current = wakePattern;

  // ── Native (Fire HD / de-Googled Android) ────────────────────────
  // When the APK provides LumaKiosk.isNativeWakeWordAvailable(), Vosk runs
  // the wake word inside the activity. The WebView only needs to listen
  // for the 'luma-native-wake' CustomEvent and push the wake word config.
  useEffect(() => {
    const bridge = getLumaBridge();
    if (!bridge) return;

    // Push the user-configured wake word into the native recognizer so
    // the grammar matches what the UI says it's listening for.
    try { bridge.setWakeWord?.(wakeWord); } catch { /* */ }

    if (!enabled) {
      try { bridge.pauseWakeWord?.(); } catch { /* */ }
      return;
    }

    try { bridge.resumeWakeWord?.(); } catch { /* */ }

    const handleWake = () => {
      if (!enabledRef.current) return;
      const now = Date.now();
      if (now - lastTriggerRef.current < COOLDOWN_MS) return;
      lastTriggerRef.current = now;
      console.log('[WakeWord] native wake received');
      onWakeRef.current();
    };
    window.addEventListener('luma-native-wake', handleWake);
    console.log('[WakeWord] Using native Vosk bridge (wake word:', wakeWord, ')');

    return () => {
      window.removeEventListener('luma-native-wake', handleWake);
      try { bridge.pauseWakeWord?.(); } catch { /* */ }
    };
  }, [enabled, wakeWord]);

  // ── Browser SpeechRecognition (desktop fallback) ─────────────────
  const stop = useCallback(() => {
    const r = recognitionRef.current;
    recognitionRef.current = null;
    if (r) {
      try { r.abort(); } catch { /* */ }
    }
  }, []);

  const start = useCallback(() => {
    if (recognitionRef.current) return;

    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.warn('[WakeWord] SpeechRecognition API not available in this browser');
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';
    recognition.maxAlternatives = 10;

    recognition.onresult = (event: any) => {
      // Guard: ignore buffered results delivered after wake word was disabled
      if (!enabledRef.current) return;

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (!result) continue;
        for (let a = 0; a < result.length; a++) {
          const alt = result[a];
          if (!alt) continue;
          const transcript = alt.transcript.toLowerCase();
          console.log('[WakeWord] transcript:', transcript);
          if (wakePatternRef.current.test(transcript)) {
            const now = Date.now();
            if (now - lastTriggerRef.current < COOLDOWN_MS) return;
            lastTriggerRef.current = now;
            // DON'T stop recognition — it keeps running always
            onWakeRef.current();
            return;
          }
        }
      }
    };

    recognition.onerror = (e: any) => {
      console.warn('[WakeWord] Error:', e.error);
      if (e.error === 'not-allowed') {
        console.error('[WakeWord] Microphone permission denied — wake word disabled');
        stop();
        return;
      }
      // Fire OS / de-Googled: Chromium will emit 'audio-capture' or
      // 'service-not-allowed' because no RecognitionService is installed.
      // Bail out permanently instead of auto-restarting forever.
      if (e.error === 'service-not-allowed' || e.error === 'audio-capture') {
        console.warn('[WakeWord] No speech service available — disabling browser fallback');
        stop();
      }
      // 'no-speech', 'aborted', 'network' — will auto-restart via onend
    };

    recognition.onend = () => {
      // Auto-restart — recognition should never stop
      if (recognitionRef.current === recognition) {
        try { recognition.start(); } catch { /* */ }
      }
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
      console.log('[WakeWord] Started browser SpeechRecognition, pattern:', wakePatternRef.current.source);
    } catch (e) {
      console.error('[WakeWord] Failed to start:', e);
    }
  }, [stop]);

  // Restart recognition when wake word changes so the new pattern takes effect.
  // Skipped entirely when the native bridge is present — that effect handles
  // the whole lifecycle above.
  useEffect(() => {
    if (getLumaBridge()) return;
    if (enabled) {
      stop();
      start();
    } else {
      stop();
    }
    return stop;
  }, [enabled, wakeWord, start, stop]);
}
