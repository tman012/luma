import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { VoiceAssistantSheet } from '@/components/voice/VoiceAssistantSheet';
import { CompactVoiceSheet } from '@/components/voice/CompactVoiceSheet';
import { NovaResultsOverlay } from '@/components/voice/NovaResultsOverlay';
import { primeVoiceAudio } from '@/hooks/useVoiceAssistant';
import { useWakeWord } from '@/hooks/useWakeWord';
import { useAuth } from '@/contexts/AuthContext';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import type { NovaResultItem } from '@/types/voice';

interface NovaResultsState {
  open: boolean;
  title: string;
  items: NovaResultItem[];
}

interface VoiceContextValue {
  voiceEnabled: boolean;
  voiceOpen: boolean;
  openVoice: () => void;
  closeVoice: () => void;
  /** Increments each time the wake word fires — sheet uses this to start listening */
  wakeSignal: number;
  /** Called by the sheet to report its current phase so wake word knows when to listen */
  setVoicePhase: (phase: 'idle' | 'listening' | 'processing' | 'speaking') => void;
  /** The configured wake word (default "alfred") */
  wakeWord: string;
  /** Show a visual grid of Nova results (called by show_results UI action). */
  showNovaResults: (title: string, items: NovaResultItem[]) => void;
  closeNovaResults: () => void;
}

const VoiceCtx = createContext<VoiceContextValue>({
  voiceEnabled: false,
  voiceOpen: false,
  openVoice: () => {},
  closeVoice: () => {},
  wakeSignal: 0,
  setVoicePhase: () => {},
  wakeWord: 'alfred',
  showNovaResults: () => {},
  closeNovaResults: () => {},
});

export function useVoice() {
  return useContext(VoiceCtx);
}

export function VoiceProvider({ children }: { children: React.ReactNode }) {
  const { authenticated } = useAuth();
  const { mode } = useLayoutMode();
  const isCompact = mode === 'compact';
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [wakeSignal, setWakeSignal] = useState(0);
  const [, setVoicePhase] = useState<'idle' | 'listening' | 'processing' | 'speaking'>('idle');
  const [wakeWord, setWakeWord] = useState('alfred');
  const [novaResults, setNovaResults] = useState<NovaResultsState>({
    open: false, title: '', items: [],
  });

  const showNovaResults = useCallback((title: string, items: NovaResultItem[]) => {
    setNovaResults({ open: true, title: title || 'Results', items: items || [] });
  }, []);
  const closeNovaResults = useCallback(() => {
    setNovaResults((s) => ({ ...s, open: false }));
  }, []);

  // Fetch voice enabled setting + wake word once auth is ready
  useEffect(() => {
    if (!authenticated) return;
    fetch('/api/kiosk/settings')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data) {
          console.log('[Voice] Settings fetched:', { voice_enabled: data.voice_enabled, wake_word: data.wake_word });
          setVoiceEnabled(!!data.voice_enabled);
          if (data.wake_word) setWakeWord(data.wake_word);
        }
      })
      .catch(() => {});
  }, [authenticated]);

  // Manual open (mic tap) — just shows the sheet in idle mode, wake word listens.
  // IMPORTANT: prime the AudioContext SYNCHRONOUSLY here because this is called
  // from inside a user-gesture click handler. iOS PWAs require audio unlock to
  // happen inside the gesture itself; async resume() from later code is silent.
  const openVoice = useCallback(() => {
    try { primeVoiceAudio(); } catch { /* */ }
    setVoiceOpen(true);
  }, []);
  const closeVoice = useCallback(() => { setVoiceOpen(false); setVoicePhase('idle'); }, []);

  // Wake word is active ONLY when the sheet is closed.
  // When the sheet is open, SpeechRecognition must be fully stopped so
  // getUserMedia can acquire the mic exclusively (mobile browsers only
  // allow one mic consumer at a time).
  const wakeWordEnabled = voiceEnabled && !voiceOpen && !isCompact;
  console.log('[Voice] wakeWordEnabled:', wakeWordEnabled, { voiceEnabled, voiceOpen, isCompact, wakeWord });

  useWakeWord({
    enabled: wakeWordEnabled,
    wakeWord,
    onWake: useCallback(() => {
      setVoiceOpen(true);
      setWakeSignal((c) => c + 1);
    }, []),
  });

  return (
    <VoiceCtx.Provider value={{
      voiceEnabled, voiceOpen, openVoice, closeVoice, wakeSignal, setVoicePhase, wakeWord,
      showNovaResults, closeNovaResults,
    }}>
      {children}
      {voiceEnabled && !isCompact && (
        <VoiceAssistantSheet open={voiceOpen} onClose={closeVoice} />
      )}
      {voiceEnabled && isCompact && (
        <CompactVoiceSheet open={voiceOpen} onClose={closeVoice} />
      )}
      <NovaResultsOverlay
        open={novaResults.open}
        title={novaResults.title}
        items={novaResults.items}
        onClose={closeNovaResults}
      />
    </VoiceCtx.Provider>
  );
}
