import { useState, useCallback, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router';
// sendVoiceCommand removed — all commands route through Alfred now
import { sendToAlfred } from '@/services/alfredApi';
import { routeVoiceCommand } from '@/services/voiceRouter';
import type { VoiceMessage, AlfredResponse, AlfredUiAction } from '@/types/voice';
import { apiFetch } from '../apiClient';

export type { VoiceMessage };
export type { AlfredResponse, AlfredUiAction };

export interface UseVoiceAssistantReturn {
  isSupported: boolean;
  isListening: boolean;
  isProcessing: boolean;
  interimText: string;
  messages: VoiceMessage[];
  startListening: () => void;
  stopListening: () => void;
  sendTextCommand: (text: string) => void;
  clearHistory: () => void;
  isSpeaking: boolean;
  stopSpeaking: () => void;
  executeUiAction: (action: AlfredUiAction) => void;
  error: string | null;
  analyserNode: AnalyserNode | null;
  audioDuration: number;
  lastUserText: string;
  lastAssistantText: string;
  /** Poster URL from the most recent pending confirmation (e.g. "Is this the one?"). Null otherwise. */
  lastPosterUrl: string | null;
}

export interface UseVoiceAssistantOptions {
  onRequestClose?: () => void;
  /** Handler for the show_results UI action — pops a results overlay. */
  onShowResults?: (title: string, items: unknown[]) => void;
}

// ---------------------------------------------------------------------------
// Audio-level Voice Activity Detection (replaces SpeechRecognition for VAD)
// ---------------------------------------------------------------------------
const SILENCE_RMS_THRESHOLD = 0.025; // RMS below this = silence (raised to ignore ambient noise)
const SILENCE_DURATION_MS = 1200;    // ms of continuous silence to finalize (was 1800 — faster cutoff)
const MAX_RECORD_MS = 15_000;        // safety cap on recording length
const SPEECH_RMS_THRESHOLD = 0.04;   // RMS above this = definite speech (higher bar than silence)

// Pick a supported audio MIME type (webm for Chrome/Android, mp4 for Safari/iOS)
function getRecorderMimeType(): string | undefined {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/ogg;codecs=opus',
  ];
  for (const mime of candidates) {
    if (MediaRecorder.isTypeSupported(mime)) return mime;
  }
  return undefined; // let browser pick default
}

// ---------------------------------------------------------------------------
// Pre-unlocked AudioContext for TTS playback.
// Mobile browsers block audio unless an AudioContext has been resumed during
// a user gesture. We create one on the first touch/click so that later
// programmatic playback (e.g. after wake word → Alfred → TTS) works.
// ---------------------------------------------------------------------------
let _sharedTtsCtx: AudioContext | null = null;

function getSharedTtsContext(): AudioContext {
  if (!_sharedTtsCtx || _sharedTtsCtx.state === 'closed') {
    _sharedTtsCtx = new AudioContext();
  }
  return _sharedTtsCtx;
}

function _unlockAudio() {
  const ctx = getSharedTtsContext();
  if (ctx.state === 'suspended') {
    ctx.resume().catch(() => {});
  }
  // Also unlock HTMLAudioElement path by playing a silent buffer
  try {
    const buf = ctx.createBuffer(1, 1, 22050);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start(0);
  } catch { /* */ }
}

/**
 * Call this synchronously inside a user gesture (click/touch) to (re)prime
 * the shared AudioContext for later TTS playback. Required on iOS PWAs where
 * the AudioContext becomes silently muted after idle periods and async
 * resume() from non-gesture code does not actually re-enable output.
 */
export function primeVoiceAudio(): void {
  _unlockAudio();
}

// One-time listener — fires on first user interaction, then removes itself
if (typeof window !== 'undefined') {
  const unlock = () => {
    _unlockAudio();
    window.removeEventListener('touchstart', unlock, true);
    window.removeEventListener('click', unlock, true);
  };
  window.addEventListener('touchstart', unlock, true);
  window.addEventListener('click', unlock, true);
}

export function useVoiceAssistant(options?: UseVoiceAssistantOptions): UseVoiceAssistantReturn {
  const [isListening, setIsListeningState] = useState(false);
  const isListeningRef = useRef(false);
  const setIsListening = useCallback((v: boolean) => { isListeningRef.current = v; setIsListeningState(v); }, []);
  const [isProcessing, setIsProcessingState] = useState(false);
  const isProcessingRef = useRef(false);
  const setIsProcessing = useCallback((v: boolean) => { isProcessingRef.current = v; setIsProcessingState(v); }, []);
  const [interimText, setInterimText] = useState('');
  const [messages, setMessages] = useState<VoiceMessage[]>([]);
  const [isSpeaking, setIsSpeakingState] = useState(false);
  const isSpeakingRef = useRef(false);
  const setIsSpeaking = useCallback((v: boolean) => { isSpeakingRef.current = v; setIsSpeakingState(v); }, []);
  const [audioDuration, setAudioDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [lastUserText, setLastUserText] = useState('');
  const [lastAssistantText, setLastAssistantText] = useState('');
  const [lastPosterUrl, setLastPosterUrl] = useState<string | null>(null);

  const pendingWatchlistRef = useRef<Record<string, unknown> | null>(null);
  const pendingFreeSpaceRef = useRef<boolean>(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const recorderChunksRef = useRef<Blob[]>([]);
  const micStreamRef = useRef<MediaStream | null>(null);
  const vadFrameRef = useRef<number>(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const vadCtxRef = useRef<AudioContext | null>(null);
  const vadAnalyserRef = useRef<AnalyserNode | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const ttsAnalyserRef = useRef<AnalyserNode | null>(null);
  const ttsAudioCtxRef = useRef<AudioContext | null>(null);
  const speakResolveRef = useRef<((reason?: string) => void) | null>(null);
  const onRequestCloseRef = useRef(options?.onRequestClose);
  onRequestCloseRef.current = options?.onRequestClose;
  const onShowResultsRef = useRef(options?.onShowResults);
  onShowResultsRef.current = options?.onShowResults;
  const navigate = useNavigate();

  // Support is driven by the actual APIs we USE — MediaRecorder + getUserMedia —
  // NOT by SpeechRecognition. The Fire HD / de-Googled Android case has no
  // Google speech service, but recording and uploading audio works fine.
  const isSupported = typeof window !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function' &&
    typeof (window as any).MediaRecorder !== 'undefined';

  // -------------------------------------------------------------------------
  // Conversation history
  // -------------------------------------------------------------------------
  const getHistory = useCallback(() => {
    return messages.slice(-10).map((m) => ({ role: m.role, content: m.content }));
  }, [messages]);

  // -------------------------------------------------------------------------
  // TTS — speak()
  //
  // Uses decodeAudioData + AudioBufferSourceNode instead of HTMLAudioElement.
  // The HTMLAudioElement + createMediaElementSource path is unreliable on
  // mobile Safari and Android Chrome with blob URLs — play() resolves but
  // audio never emits. AudioBufferSourceNode plays reliably as long as the
  // AudioContext is unlocked (handled by the one-time touch listener above).
  // -------------------------------------------------------------------------
  const bufferSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const speak = useCallback((text: string): Promise<void> => {
    if (!text) { console.warn('[TTS] speak() called with empty text'); return Promise.resolve(); }

    console.log('[TTS] speak() START — text:', text.slice(0, 80));
    // Stop any previous buffer source
    if (bufferSourceRef.current) {
      try { bufferSourceRef.current.onended = null; bufferSourceRef.current.stop(); } catch { /* */ }
      bufferSourceRef.current = null;
    }
    ttsAnalyserRef.current = null;
    setIsSpeaking(true);

    return (async () => {
      try {
        // --- 1. Fetch TTS audio as ArrayBuffer ---
        // 60s cap: OpenAI/ElevenLabs can take 15-25s to stream a long reply
        // (recipe ingredient summaries, multi-sentence answers). A 15s cap
        // here was truncating the ArrayBuffer mid-stream, which decoded to a
        // shorter audio buffer that appeared to "cut off" mid-sentence.
        const abort = new AbortController();
        const timer = setTimeout(() => abort.abort(), 60_000);
        let resp: Response;
        console.log('[TTS] Fetching audio from /api/voice/tts...');
        try {
          resp = await apiFetch('/api/voice/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
            signal: abort.signal,
          });
        } finally { clearTimeout(timer); }

        console.log('[TTS] Fetch response:', resp.status, resp.statusText);
        if (!resp.ok) {
          console.error('[TTS] Response not ok:', resp.status, resp.statusText);
          setIsSpeaking(false);
          return;
        }

        const arrayBuffer = await resp.arrayBuffer();
        console.log('[TTS] ArrayBuffer received, size:', arrayBuffer.byteLength);
        if (arrayBuffer.byteLength < 100) {
          console.error('[TTS] Response too small:', arrayBuffer.byteLength);
          setIsSpeaking(false);
          return;
        }

        // --- 2. Resume the pre-unlocked AudioContext ---
        const ctx = getSharedTtsContext();
        if (ctx.state === 'suspended') {
          try { await ctx.resume(); } catch (e) { console.warn('[TTS] ctx.resume() failed:', e); }
        }
        ttsAudioCtxRef.current = ctx;
        console.log('[TTS] AudioContext state:', ctx.state);

        // --- 3. Decode the audio ---
        let audioBuffer: AudioBuffer;
        try {
          // Safari requires the old callback-style decodeAudioData for some
          // formats; use Promise form first and fall back.
          audioBuffer = await new Promise<AudioBuffer>((resolve, reject) => {
            // Copy the buffer — decodeAudioData detaches it, and some Safari
            // versions need a fresh copy to decode reliably.
            const copy = arrayBuffer.slice(0);
            try {
              const p = ctx.decodeAudioData(copy, resolve, reject);
              if (p && typeof (p as Promise<AudioBuffer>).then === 'function') {
                (p as Promise<AudioBuffer>).then(resolve, reject);
              }
            } catch (e) {
              reject(e);
            }
          });
        } catch (e) {
          console.error('[TTS] decodeAudioData failed:', e);
          setIsSpeaking(false);
          return;
        }
        console.log('[TTS] Decoded — duration:', audioBuffer.duration, 'channels:', audioBuffer.numberOfChannels);
        setAudioDuration(audioBuffer.duration);

        // --- 4. Build graph: BufferSource → Analyser → Destination ---
        const source = ctx.createBufferSource();
        source.buffer = audioBuffer;
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.4;
        source.connect(analyser);
        analyser.connect(ctx.destination);
        ttsAnalyserRef.current = analyser;
        bufferSourceRef.current = source;

        // --- 5. Play and await completion ---
        await new Promise<void>((resolve) => {
          let resolved = false;
          const timers: ReturnType<typeof setTimeout>[] = [];
          const done = (reason: string) => {
            if (resolved) return;
            resolved = true;
            console.log('[TTS] done() called, reason:', reason);
            timers.forEach(clearTimeout);
            speakResolveRef.current = null;
            ttsAnalyserRef.current = null;
            setIsSpeaking(false);
            setAudioDuration(0);
            try { source.onended = null; source.stop(); } catch { /* already stopped */ }
            if (bufferSourceRef.current === source) bufferSourceRef.current = null;
            resolve();
          };
          // Safety timeout: duration + 5s buffer, or 2-minute hard cap
          const safetyMs = Math.min(120_000, Math.ceil((audioBuffer.duration + 5) * 1000));
          timers.push(setTimeout(() => done('safety-timeout'), safetyMs));
          speakResolveRef.current = () => done('stopSpeaking-called');
          source.onended = () => done('ended');
          try {
            source.start(0);
            console.log('[TTS] source.start(0) called — playing');
          } catch (e) {
            console.error('[TTS] source.start() threw:', e);
            done('start-failed');
          }
        });
      } catch (e) {
        console.error('[TTS] speak() failed:', e);
        setIsSpeaking(false);
      }
    })();
  }, []);

  // -------------------------------------------------------------------------
  // Messages
  // -------------------------------------------------------------------------
  const addMessage = useCallback((
    role: 'user' | 'assistant', content: string,
    source?: 'luma' | 'alfred', alfredResponse?: AlfredResponse,
  ) => {
    setMessages((prev) => [...prev, { role, content, timestamp: Date.now(), source, alfredResponse }]);
  }, []);

  const executeUiAction = useCallback((action: AlfredUiAction) => {
    switch (action.action) {
      case 'navigate':
        if (typeof action.path === 'string') navigate(action.path);
        break;
      case 'open_search':
      case 'show_search_results':
        if (typeof action.query === 'string') {
          // Navigate home first (in case we're on another page), then trigger search
          navigate('/');
          setTimeout(() => {
            window.dispatchEvent(new CustomEvent('search-query', {
              detail: { query: action.query, tab: action.tab || 'all', personId: action.person_id },
            }));
          }, 100);
        }
        break;
      case 'open_downloads':
        navigate('/now-available');
        break;
      case 'open_describe_results':
        if (typeof action.description === 'string') {
          navigate(`/describe-it`);
        }
        break;
      case 'open_modal': {
        // Deep-link to modal via URL params — AppModalHost handles the rest
        const params = new URLSearchParams();
        params.set('modal', action.media_type === 'tv' ? 'tv' : 'movie');
        if (action.tmdb_id) params.set('tmdb', String(action.tmdb_id));
        if (action.season) params.set('season', String(action.season));
        navigate(`/?${params.toString()}`);
        break;
      }
      case 'show_results': {
        const title = typeof action.title === 'string' ? action.title : 'Results';
        const items = Array.isArray(action.items) ? action.items : [];
        onShowResultsRef.current?.(title, items);
        break;
      }
    }
  }, [navigate]);

  // -------------------------------------------------------------------------
  // Whisper transcription
  // -------------------------------------------------------------------------
  const transcribeWithWhisper = useCallback(async (audioBlob: Blob): Promise<string | null> => {
    try {
      const form = new FormData();
      // Use correct extension based on blob type so Whisper gets the right format
      const ext = audioBlob.type.includes('mp4') ? 'mp4' : audioBlob.type.includes('ogg') ? 'ogg' : 'webm';
      form.append('audio', audioBlob, `recording.${ext}`);
      form.append('language', 'en');
      const resp = await apiFetch('/api/voice/transcribe', { method: 'POST', body: form });
      if (!resp.ok) return null;
      const data = await resp.json();
      return (data.text || '').trim() || null;
    } catch { return null; }
  }, []);

  // -------------------------------------------------------------------------
  // Process command
  // -------------------------------------------------------------------------
  const startListeningRef = useRef<(() => void) | null>(null);

  // Only re-listen when Alfred explicitly needs a follow-up (pending action
  // awaiting confirmation, or a clarification request). Simple question marks
  // in the response (e.g. "How can I help you?") should NOT trigger re-listen
  // because the session would capture silence, get stuck, and kill the wake word.
  const needsFollowUp = useCallback((_message: string, status?: string, type?: string): boolean => {
    if (status === 'pending') return true;
    if (type === 'clarification') return true;
    return false;
  }, []);

  // After TTS: either auto-listen for a follow-up (pending responses) or
  // go idle. We NO LONGER auto-close the sheet on terminal responses —
  // the sheet stays visible until the user taps close, so they can see
  // the transcript and ask a natural follow-up without re-opening voice.
  const afterSpeak = useCallback((message: string, status?: string, type?: string) => {
    if (needsFollowUp(message, status, type)) {
      console.log('[Voice] Follow-up expected, listening again');
      setIsProcessing(false); // Clear purple immediately so orb goes straight to blue
      startListeningRef.current?.();
    } else {
      console.log('[Voice] Terminal response, going idle (sheet stays open)');
      setIsProcessing(false);
      setIsListening(false);
    }
  }, [needsFollowUp]);

  const processCommand = useCallback(async (transcript: string) => {
    setIsListening(false);
    setInterimText('');
    setLastUserText(transcript);
    setLastAssistantText('');
    setLastPosterUrl(null);
    addMessage('user', transcript);
    setIsProcessing(true);

    try {
      const route = routeVoiceCommand(transcript);

      if (route.target === 'local_luma' && route.localAction) {
        const action = route.localAction;
        switch (action.type) {
          case 'dismiss': {
            // Silent exit — no TTS, no backend call, no navigation.
            console.log('[Voice] Dismiss intent — closing silently');
            pendingWatchlistRef.current = null;
            onRequestCloseRef.current?.();
            setIsProcessing(false);
            setIsListening(false);
            break;
          }
          case 'navigate': {
            // Close any open modal before navigating
            window.dispatchEvent(new Event('voice-navigate-home'));
            if (action.path === 'back') navigate(-1 as any);
            else navigate(action.path);
            onRequestCloseRef.current?.();
            break;
          }
          case 'reset_alfred': {
            try {
              await apiFetch('/api/alfred/reset', { method: 'POST' });
              setLastAssistantText('Alfred has been reset.');
              addMessage('assistant', 'Alfred has been reset.', 'luma');
              await speak('Alfred has been reset.');
            } catch {
              setLastAssistantText('Sorry, I could not reset Alfred.');
              addMessage('assistant', 'Failed to reset Alfred.', 'luma');
              await speak('Sorry, I could not reset Alfred.');
            }
            afterSpeak('done');
            break;
          }
        }
        return;
      }

      // Alfred / ambiguous
      console.log('[Voice] Sending to Alfred:', transcript);
      const alfredContext: Record<string, unknown> = { currentPath: window.location.pathname };
      if (pendingWatchlistRef.current) {
        alfredContext.pendingWatchlist = pendingWatchlistRef.current;
        pendingWatchlistRef.current = null;
      }
      if (pendingFreeSpaceRef.current) {
        alfredContext.pendingFreeSpace = true;
        pendingFreeSpaceRef.current = false;
      }
      const alfredResponse = await sendToAlfred(transcript, alfredContext, getHistory());
      console.log('[Voice] Alfred response:', alfredResponse.message);
      setLastAssistantText(alfredResponse.message);
      // Show poster only for pending confirmations (e.g. "I found X, is this the one?")
      const posterUrl = alfredResponse.status === 'pending'
        ? ((alfredResponse.data?.poster_url as string | null | undefined) ?? null)
        : null;
      setLastPosterUrl(posterUrl);
      addMessage('assistant', alfredResponse.message, 'alfred', alfredResponse);

      // Store pending watchlist context for follow-up confirmation
      const pendingWl = alfredResponse.data?.pending_watchlist as Record<string, unknown> | undefined;
      const hasPending = alfredResponse.status === 'pending' && !!pendingWl;
      if (hasPending) {
        pendingWatchlistRef.current = pendingWl!;
      }
      if (alfredResponse.status === 'pending' && alfredResponse.data?.pending_free_space) {
        pendingFreeSpaceRef.current = true;
      }

      const uiAction = alfredResponse.suggested_ui_action;
      // open_modal just adds query params — safe to fire before TTS.
      // show_results pops the visual overlay — also fire immediately.
      // Both are visual-only outputs: skip TTS entirely.
      const isModalAction = uiAction?.action === 'open_modal';
      const isShowResults = uiAction?.action === 'show_results';
      const isVisualOnly = isModalAction || isShowResults;

      if (isVisualOnly) {
        executeUiAction(uiAction!);
        onRequestCloseRef.current?.();
      }

      // Skip TTS for visual-only outputs — the user said "show me", they want
      // to SEE the answer, not hear it. Empty messages also skip TTS.
      const shouldSpeak = !isShowResults && !!alfredResponse.message?.trim();
      if (shouldSpeak) {
        console.log('[Voice] Speaking TTS...');
        await speak(alfredResponse.message);
        console.log('[Voice] TTS done');
      } else {
        console.log('[Voice] Visual-only response, skipping TTS');
        setIsSpeaking(false);
      }

      if (hasPending) {
        // Pending follow-up (e.g. watchlist offer) — auto-listen for yes/no
        // Clear processing immediately so orb goes straight to blue (no purple flash)
        console.log('[Voice] Pending follow-up, listening for response');
        setIsProcessing(false);
        startListeningRef.current?.();
      } else if (uiAction && !isVisualOnly) {
        executeUiAction(uiAction);
        onRequestCloseRef.current?.();
      } else if (!uiAction) {
        afterSpeak(alfredResponse.message, alfredResponse.status, alfredResponse.type);
      } else if (isVisualOnly) {
        // Visual-only: tear down processing/listening state, sheet already closed
        setIsProcessing(false);
        setIsListening(false);
      }

    } catch (err) {
      console.error('[Voice] processCommand error:', err);
      const errorText = err instanceof Error ? err.message : 'Something went wrong.';
      setLastAssistantText(errorText);
      addMessage('assistant', errorText);
      await speak(errorText);
      afterSpeak('error');
    } finally {
      setIsProcessing(false);
    }
  }, [addMessage, speak, navigate, getHistory, executeUiAction, afterSpeak]);

  // -------------------------------------------------------------------------
  // Finalize recording → Whisper → processCommand
  // -------------------------------------------------------------------------
  const finalizeRecording = useCallback(() => {
    // Stop VAD polling
    if (vadFrameRef.current) { cancelAnimationFrame(vadFrameRef.current); vadFrameRef.current = 0; }

    // Stop recorder
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop(); // triggers onstop → handleChunks
    }

    // Stop mic
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
    }

    // Clean up VAD audio context
    if (vadCtxRef.current) {
      try { vadCtxRef.current.close(); } catch { /* */ }
      vadCtxRef.current = null;
      vadAnalyserRef.current = null;
    }
  }, []);

  const handleChunks = useCallback(async (chunks: Blob[]) => {
    setIsListening(false);
    setInterimText('');

    if (chunks.length === 0) {
      console.log('[Voice] No audio chunks, closing');
      onRequestCloseRef.current?.();
      return;
    }
    const audioBlob = new Blob(chunks, { type: chunks[0]?.type || 'audio/webm' });
    if (audioBlob.size < 500) {
      console.log('[Voice] Audio too small, closing');
      onRequestCloseRef.current?.();
      return;
    }

    setIsProcessing(true);
    const transcript = await transcribeWithWhisper(audioBlob);

    if (transcript) {
      // processCommand manages its own isProcessing state
      processCommand(transcript);
    } else {
      console.log('[Voice] No transcript from Whisper, closing');
      setIsProcessing(false);
      onRequestCloseRef.current?.();
    }
  }, [transcribeWithWhisper, processCommand]);

  // -------------------------------------------------------------------------
  // Start listening — MediaRecorder + audio-level VAD (NO SpeechRecognition)
  // -------------------------------------------------------------------------
  const startListening = useCallback(async (retryCount = 0) => {
    // Don't double-start; also don't start if already processing/speaking
    // Use refs for latest values — React state batching makes closure values stale
    if (isListeningRef.current || isProcessingRef.current || isSpeakingRef.current) return;

    // Stop any lingering recording
    if (recorderRef.current) return;

    // Set listening BEFORE the async getUserMedia so the orb turns blue
    // immediately — no purple idle frame while waiting for mic access.
    setIsListening(true);
    setError(null);
    setInterimText('');

    // If the native Vosk wake-word recognizer is running (Fire HD APK), it
    // owns the mic via AudioRecord. VoiceContext already pauses it when the
    // sheet opens, but we defensively pause here too to close any race
    // between the sheet mount and the getUserMedia call on this code path.
    // Resume is NOT our responsibility — VoiceContext re-enables the wake
    // word automatically when voiceOpen flips back to false.
    try {
      const bridge = (window as unknown as {
        LumaKiosk?: { isNativeWakeWordAvailable?: () => boolean; pauseWakeWord?: () => void };
      }).LumaKiosk;
      if (bridge?.isNativeWakeWordAvailable?.()) {
        bridge.pauseWakeWord?.();
      }
    } catch { /* bridge is optional */ }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      // Mic busy (SpeechRecognition still releasing) — retry once after a short pause
      if ((err.name === 'NotReadableError' || err.name === 'AbortError') && retryCount < 2) {
        console.log(`[Voice] Mic busy, retrying in 150ms (attempt ${retryCount + 1})`);
        // Keep isListening true across retries — orb stays blue
        retryTimerRef.current = setTimeout(() => {
          isListeningRef.current = false; // allow re-entry
          startListening(retryCount + 1);
        }, 150);
        return;
      }
      if (err.name === 'NotAllowedError') setError('Microphone access denied.');
      else if (err.name === 'NotFoundError') setError('No microphone found.');
      else setError(`Microphone error: ${err.message}`);
      setIsListening(false);
      return;
    }

    // For a fresh session clear previous text/poster.
    // When re-listening for a pending follow-up (e.g. "yes/no" after a confirmation),
    // keep the previous response visible so the user sees it while the orb listens.
    if (!pendingWatchlistRef.current) {
      setLastUserText('');
      setLastAssistantText('');
      setLastPosterUrl(null);
    }
    micStreamRef.current = stream;

    // --- MediaRecorder ---
    recorderChunksRef.current = [];
    let recorder: MediaRecorder;
    try {
      const mimeType = getRecorderMimeType();
      const opts: MediaRecorderOptions = mimeType ? { mimeType } : {};
      recorder = new MediaRecorder(stream, opts);
      console.log('[Voice] MediaRecorder created, mimeType:', recorder.mimeType);
    } catch (recErr) {
      console.error('[Voice] MediaRecorder failed to create:', recErr);
      stream.getTracks().forEach((t) => t.stop());
      setError('Recording not supported on this browser.');
      setIsListening(false);
      return;
    }
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) recorderChunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      handleChunks(recorderChunksRef.current);
    };
    recorder.start(250);
    recorderRef.current = recorder;

    // --- Audio-level VAD ---
    try {
      const ctx = new AudioContext();
      vadCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      vadAnalyserRef.current = analyser;

      const dataArray = new Float32Array(analyser.fftSize);
      let silenceStart = 0;
      let speechDetected = false;
      const recordStart = Date.now();
      // Adaptive noise floor — sample ambient level over first 200ms
      let noiseFloor = SILENCE_RMS_THRESHOLD;
      let noiseSum = 0;
      let noiseSamples = 0;
      const NOISE_CALIBRATION_MS = 200;

      const checkLevel = () => {
        // Bail if recording was already stopped
        if (!recorderRef.current) return;

        analyser.getFloatTimeDomainData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) sum += dataArray[i]! * dataArray[i]!;
        const rms = Math.sqrt(sum / dataArray.length);

        const now = Date.now();
        const elapsed = now - recordStart;

        // Calibrate noise floor from ambient level during first 200ms
        if (elapsed < NOISE_CALIBRATION_MS) {
          noiseSum += rms;
          noiseSamples++;
          vadFrameRef.current = requestAnimationFrame(checkLevel);
          return;
        }
        if (noiseSamples > 0 && elapsed < NOISE_CALIBRATION_MS + 50) {
          // Set noise floor to 2x the average ambient level (minimum: default threshold)
          noiseFloor = Math.max(SILENCE_RMS_THRESHOLD, (noiseSum / noiseSamples) * 2);
          noiseSamples = 0; // only calibrate once
          console.log('[Voice] Noise floor calibrated:', noiseFloor.toFixed(4));
        }

        // Speech = clearly above both noise floor and speech threshold
        // Silence = below noise floor
        // Ambiguous (between thresholds) = don't reset silence timer, let countdown continue
        const isSpeech = rms > SPEECH_RMS_THRESHOLD && rms > noiseFloor;

        if (isSpeech) {
          silenceStart = 0;
          speechDetected = true;
        } else if (speechDetected) {
          // Once speech detected, any non-speech frame counts toward silence
          if (!silenceStart) silenceStart = now;
          if (now - silenceStart >= SILENCE_DURATION_MS) {
            finalizeRecording();
            return;
          }
        }

        // Safety cap
        if (elapsed >= MAX_RECORD_MS) {
          finalizeRecording();
          return;
        }

        vadFrameRef.current = requestAnimationFrame(checkLevel);
      };
      vadFrameRef.current = requestAnimationFrame(checkLevel);
    } catch {
      // If AudioContext fails, use a simple timeout fallback
      setTimeout(() => finalizeRecording(), 5000);
    }
  }, [isListening, isProcessing, isSpeaking, handleChunks, finalizeRecording]);

  // Keep ref in sync so afterSpeak can trigger listening for follow-ups
  startListeningRef.current = startListening;

  // -------------------------------------------------------------------------
  // Stop listening (manual)
  // -------------------------------------------------------------------------
  const stopListening = useCallback(() => {
    if (vadFrameRef.current) { cancelAnimationFrame(vadFrameRef.current); vadFrameRef.current = 0; }
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder && recorder.state !== 'inactive') {
      recorder.onstop = null;
      try { recorder.stop(); } catch { /* */ }
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
    }
    if (vadCtxRef.current) {
      try { vadCtxRef.current.close(); } catch { /* */ }
      vadCtxRef.current = null;
      vadAnalyserRef.current = null;
    }
    setIsListening(false);
    setInterimText('');
  }, []);

  const sendTextCommand = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isProcessing) return;
    processCommand(trimmed);
  }, [processCommand, isProcessing]);

  const clearHistory = useCallback(() => { setMessages([]); }, []);

  const stopSpeaking = useCallback(() => {
    console.log('[TTS] stopSpeaking() called');
    if (bufferSourceRef.current) {
      try { bufferSourceRef.current.onended = null; bufferSourceRef.current.stop(); } catch { /* */ }
      bufferSourceRef.current = null;
    }
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    if (speakResolveRef.current) speakResolveRef.current('stopSpeaking-external');
    else setIsSpeaking(false);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (vadFrameRef.current) cancelAnimationFrame(vadFrameRef.current);
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      if (recorderRef.current && recorderRef.current.state !== 'inactive') {
        recorderRef.current.onstop = null;
        try { recorderRef.current.stop(); } catch { /* */ }
      }
      if (micStreamRef.current) micStreamRef.current.getTracks().forEach((t) => t.stop());
      if (vadCtxRef.current) { try { vadCtxRef.current.close(); } catch { /* */ } }
      if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
      if (bufferSourceRef.current) {
        try { bufferSourceRef.current.onended = null; bufferSourceRef.current.stop(); } catch { /* */ }
        bufferSourceRef.current = null;
      }
      // Don't close ttsAudioCtxRef — it's the shared pre-unlocked context
      if (speakResolveRef.current) speakResolveRef.current('unmount');
    };
  }, []);

  return {
    isSupported,
    isListening,
    isProcessing,
    interimText,
    messages,
    startListening,
    stopListening,
    sendTextCommand,
    clearHistory,
    isSpeaking,
    stopSpeaking,
    executeUiAction,
    error,
    analyserNode: ttsAnalyserRef.current,
    audioDuration,
    lastUserText,
    lastAssistantText,
    lastPosterUrl,
  };
}
