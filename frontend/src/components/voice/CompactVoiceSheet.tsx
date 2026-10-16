import { useEffect, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useVoiceAssistant, primeVoiceAudio } from '@/hooks/useVoiceAssistant';
import { useVoice } from '@/contexts/VoiceContext';

interface CompactVoiceSheetProps {
  open: boolean;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Audio-reactive bar visualizer — lightweight, mobile-friendly
// ---------------------------------------------------------------------------
function WaveformBars({
  analyserNode,
  active,
  hue,
}: {
  analyserNode: AnalyserNode | null;
  active: boolean;
  hue: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const stateRef = useRef({ analyserNode, active, hue });
  stateRef.current = { analyserNode, active, hue };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    const BAR_COUNT = 28;
    const freq = new Uint8Array(1024);
    let phase = 0;

    const draw = () => {
      const { analyserNode: node, active: isActive, hue: h } = stateRef.current;
      const rect = canvas.getBoundingClientRect();
      const W = rect.width;
      const H = rect.height;
      ctx.clearRect(0, 0, W, H);

      phase += 0.05;
      let amp = 0;
      if (node) {
        const N = Math.min(freq.length, node.frequencyBinCount);
        node.getByteFrequencyData(freq as unknown as Uint8Array<ArrayBuffer>);
        let sum = 0;
        for (let i = 0; i < N; i++) sum += freq[i]!;
        amp = (sum / N) / 255;
      }

      const gap = 4;
      const barW = Math.max(2, (W - gap * (BAR_COUNT - 1)) / BAR_COUNT);
      const midY = H / 2;

      for (let i = 0; i < BAR_COUNT; i++) {
        // Sample frequency data across the spectrum
        let v = 0;
        if (node && amp > 0.01) {
          const binIdx = Math.floor((i / BAR_COUNT) * 128) + 4;
          v = (freq[binIdx] || 0) / 255;
        }
        // Add a gentle idle shimmer so bars never flatline when active
        const idle = isActive ? 0.1 + Math.sin(phase + i * 0.4) * 0.05 : 0.04 + Math.sin(phase * 0.6 + i * 0.3) * 0.02;
        const h01 = Math.max(idle, v);
        const barH = Math.max(3, h01 * H * 0.9);

        const x = i * (barW + gap);
        const y = midY - barH / 2;

        // Hue shifts slightly across the width for a nice gradient effect
        const localHue = h + (i / BAR_COUNT) * 30 - 15;
        const alpha = isActive ? 0.85 : 0.4;
        ctx.fillStyle = `hsla(${localHue}, 85%, 65%, ${alpha})`;

        // Rounded bars
        const r = barW / 2;
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + barW, y, x + barW, y + barH, r);
        ctx.arcTo(x + barW, y + barH, x, y + barH, r);
        ctx.arcTo(x, y + barH, x, y, r);
        ctx.arcTo(x, y, x + barW, y, r);
        ctx.closePath();
        ctx.fill();
      }

      animRef.current = requestAnimationFrame(draw);
    };
    animRef.current = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(animRef.current);
      ro.disconnect();
    };
  }, []);

  return <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />;
}

// ---------------------------------------------------------------------------
// CompactVoiceSheet — bottom sheet voice assistant for mobile / compact mode
//
// Outer wrapper fully unmounts the inner sheet (and therefore useVoiceAssistant)
// when closed. This guarantees no stuck isProcessing/isSpeaking state can
// survive across open sessions.
// ---------------------------------------------------------------------------
export function CompactVoiceSheet({ open, onClose }: CompactVoiceSheetProps) {
  const [mounted, setMounted] = useState(open);
  useEffect(() => {
    if (open) {
      setMounted(true);
      return;
    }
    // Delay unmount by the exit animation duration so the slide-down is visible
    const t = setTimeout(() => setMounted(false), 400);
    return () => clearTimeout(t);
  }, [open]);

  if (!mounted) return null;
  return <CompactVoiceSheetInner open={open} onClose={onClose} />;
}

function CompactVoiceSheetInner({ open, onClose }: CompactVoiceSheetProps) {
  const voice = useVoice();
  const {
    isListening,
    isProcessing,
    isSpeaking,
    startListening,
    stopListening,
    stopSpeaking,
    clearHistory,
    error,
    analyserNode,
    lastAssistantText,
    lastUserText,
    lastPosterUrl,
  } = useVoiceAssistant({
    onRequestClose: onClose,
    onShowResults: (title, items) => voice.showNovaResults(title, items as any),
  });

  const { setVoicePhase, wakeWord } = voice;
  const [entered, setEntered] = useState(false);
  // Local display text — only shows responses from the CURRENT session.
  // Prevents stale lastAssistantText from a previous session leaking in.
  const [displayText, setDisplayText] = useState('');
  const [displayPosterUrl, setDisplayPosterUrl] = useState<string | null>(null);
  const sessionActiveRef = useRef(false);

  // Report phase for any consumers
  useEffect(() => {
    if (isSpeaking) setVoicePhase('speaking');
    else if (isProcessing) setVoicePhase('processing');
    else if (isListening) setVoicePhase('listening');
    else setVoicePhase('idle');
  }, [isListening, isProcessing, isSpeaking, setVoicePhase]);

  // Slide-in animation + session lifecycle
  useEffect(() => {
    if (open) {
      sessionActiveRef.current = true;
      setDisplayText('');
      const t = setTimeout(() => setEntered(true), 20);
      return () => clearTimeout(t);
    }
    // On close: halt any in-flight TTS / listening so state doesn't get stuck
    sessionActiveRef.current = false;
    setEntered(false);
    setDisplayText('');
    setDisplayPosterUrl(null);
    try { stopSpeaking(); } catch { /* */ }
    try { stopListening(); } catch { /* */ }
  }, [open, stopSpeaking, stopListening]);

  // Capture assistant responses that arrive during the current session only
  useEffect(() => {
    if (!sessionActiveRef.current) return;
    if (lastAssistantText) setDisplayText(lastAssistantText);
  }, [lastAssistantText]);

  useEffect(() => {
    if (!sessionActiveRef.current) return;
    setDisplayPosterUrl(lastPosterUrl);
  }, [lastPosterUrl]);

  // After speaking finishes, hold the response text for a few seconds
  // then fade back to the idle "Tap to talk to Nova" prompt.
  useEffect(() => {
    if (!open || !displayText) return;
    // Only start the timer when we're in true idle (done speaking, not
    // listening, not processing). If the user taps mic again or a
    // follow-up kicks in, the displayText will be replaced naturally.
    if (isSpeaking || isProcessing || isListening) return;
    const t = setTimeout(() => { setDisplayText(''); setDisplayPosterUrl(null); }, 4000);
    return () => clearTimeout(t);
  }, [open, displayText, isSpeaking, isProcessing, isListening]);

  // Idle auto-close removed — the sheet stays open until the user taps
  // away. The stuck-processing watchdog below still fires if something
  // genuinely hangs, as a recovery mechanism.

  // Stuck-state watchdog: if processing hasn't resolved in 75s (TTS
  // hung, network stall, etc.), force-close the sheet. 75s covers even
  // the worst cold-start TTS latency. Closing unmounts the hook's effect
  // chain so isProcessing can't remain stuck on next open.
  useEffect(() => {
    if (!open || !isProcessing) return;
    const t = setTimeout(() => {
      console.warn('[CompactVoice] Processing stuck >75s, forcing close');
      try { stopSpeaking(); } catch { /* */ }
      try { stopListening(); } catch { /* */ }
      onClose();
    }, 75_000);
    return () => clearTimeout(t);
  }, [open, isProcessing, stopSpeaking, stopListening, onClose]);

  // Auto-start listening when the sheet first opens (mobile mic-tap flow)
  const hasAutoStarted = useRef(false);
  useEffect(() => {
    if (!open) {
      hasAutoStarted.current = false;
      return;
    }
    if (hasAutoStarted.current) return;
    hasAutoStarted.current = true;
    clearHistory();
    // Small delay lets the sheet paint before we acquire the mic
    const t = setTimeout(() => startListening(), 220);
    return () => clearTimeout(t);
  }, [open, clearHistory, startListening]);

  const handleMicTap = useCallback(() => {
    // Re-prime audio inside the gesture — critical for iOS PWA TTS playback
    try { primeVoiceAudio(); } catch { /* */ }
    // Hard cancel path — if we're in ANY non-idle state, stop everything
    // and reset the session so the user can always escape a stuck state.
    if (isSpeaking || isListening || isProcessing) {
      try { stopSpeaking(); } catch { /* */ }
      try { stopListening(); } catch { /* */ }
      setDisplayText('');
      return;
    }
    // Idle → start a fresh listening session
    clearHistory();
    setDisplayText('');
    startListening();
  }, [isListening, isProcessing, isSpeaking, startListening, stopListening, stopSpeaking, clearHistory]);

  // Hue shifts with state: purple idle, blue listening, violet processing, cyan speaking
  const hue = isSpeaking ? 190 : isListening ? 210 : isProcessing ? 275 : 260;
  const statusText = error
    ? error
    : isSpeaking
    ? ''
    : isProcessing
    ? 'Thinking…'
    : isListening
    ? 'Listening…'
    : `Tap to talk to ${wakeWord.charAt(0).toUpperCase() + wakeWord.slice(1)}`;

  const primaryText = displayText || (isListening ? lastUserText : '');

  if (!open && !entered) {
    // Still allow React to unmount cleanly, but we always render via portal while open
  }

  return createPortal(
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10001,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
        pointerEvents: open ? 'auto' : 'none',
        visibility: open ? 'visible' : 'hidden',
      }}
      aria-hidden={!open}
    >
      {/* Scrim */}
      <div
        onClick={onClose}
        style={{
          position: 'absolute',
          inset: 0,
          background: 'rgba(0, 0, 0, 0.6)',
          backdropFilter: 'blur(8px)',
          WebkitBackdropFilter: 'blur(8px)',
          opacity: entered && open ? 1 : 0,
          transition: 'opacity 0.25s ease-out',
        }}
      />

      {/* Sheet */}
      <div
        style={{
          position: 'relative',
          width: '100%',
          maxHeight: '72vh',
          background: `linear-gradient(180deg,
            hsla(${hue}, 40%, 10%, 0.98) 0%,
            hsla(${hue - 10}, 35%, 6%, 0.98) 100%)`,
          borderTopLeftRadius: 28,
          borderTopRightRadius: 28,
          boxShadow: '0 -20px 60px rgba(0,0,0,0.6)',
          paddingTop: 10,
          paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 32px)',
          transform: entered && open ? 'translateY(0)' : 'translateY(100%)',
          transition: 'transform 0.35s cubic-bezier(0.16, 1, 0.3, 1)',
          overflow: 'hidden',
          borderTop: `1px solid hsla(${hue}, 70%, 60%, 0.25)`,
        }}
      >
        {/* Ambient glow behind content */}
        <div
          style={{
            position: 'absolute',
            top: -120,
            left: '50%',
            transform: 'translateX(-50%)',
            width: 320,
            height: 320,
            borderRadius: '50%',
            background: `radial-gradient(circle, hsla(${hue}, 85%, 55%, ${isListening || isSpeaking ? 0.25 : 0.12}) 0%, transparent 70%)`,
            filter: 'blur(40px)',
            pointerEvents: 'none',
            transition: 'background 0.4s ease-out',
          }}
        />

        {/* Drag handle */}
        <div
          style={{
            width: 44,
            height: 4,
            background: 'rgba(255,255,255,0.25)',
            borderRadius: 2,
            margin: '0 auto 18px',
          }}
        />

        {/* Header row */}
        <div
          style={{
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '0 20px',
            marginBottom: 18,
          }}
        >
          <div
            style={{
              fontSize: 17,
              fontWeight: 600,
              letterSpacing: '0.02em',
              color: 'white',
            }}
          >
            Hi, I'm {wakeWord.charAt(0).toUpperCase() + wakeWord.slice(1)}
          </div>
        </div>

        {/* Waveform visualizer */}
        <div
          style={{
            height: 110,
            padding: '0 24px',
            marginBottom: 8,
          }}
        >
          <WaveformBars analyserNode={analyserNode} active={isListening || isSpeaking} hue={hue} />
        </div>

        {/* Status / transcript */}
        <div
          style={{
            minHeight: 56,
            padding: '0 28px',
            textAlign: displayPosterUrl ? 'left' : 'center',
            display: 'flex',
            flexDirection: displayPosterUrl ? 'row' : 'column',
            alignItems: 'center',
            gap: displayPosterUrl ? 14 : 6,
            justifyContent: displayPosterUrl ? 'flex-start' : 'center',
          }}
        >
          {displayPosterUrl && (
            <img
              src={displayPosterUrl}
              alt="Poster"
              style={{
                width: 56,
                height: 84,
                objectFit: 'cover',
                borderRadius: 6,
                flexShrink: 0,
                boxShadow: '0 2px 12px rgba(0,0,0,0.6)',
                animation: 'cvFadeIn 0.3s ease-out',
              }}
            />
          )}
          {primaryText ? (
            <p
              style={{
                fontSize: 17,
                lineHeight: 1.4,
                color: 'rgba(255,255,255,0.95)',
                fontWeight: 400,
                margin: 0,
                maxHeight: 100,
                overflow: 'hidden',
                animation: 'cvFadeIn 0.3s ease-out',
              }}
            >
              {primaryText}
            </p>
          ) : (
            <p
              style={{
                fontSize: 15,
                color: error ? '#ff8080' : 'rgba(255,255,255,0.55)',
                fontWeight: 400,
                margin: 0,
                letterSpacing: '0.01em',
              }}
            >
              {statusText}
            </p>
          )}
        </div>

        {/* Mic button */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'center',
            marginTop: 22,
          }}
        >
          <button
            type="button"
            onClick={handleMicTap}
            aria-label={isProcessing || isSpeaking ? 'Cancel' : isListening ? 'Stop listening' : 'Start listening'}
            style={{
              position: 'relative',
              width: 78,
              height: 78,
              borderRadius: '50%',
              border: 'none',
              cursor: 'pointer',
              background: `radial-gradient(circle at 30% 30%,
                hsla(${hue}, 90%, 70%, 1) 0%,
                hsla(${hue + 10}, 85%, 50%, 1) 50%,
                hsla(${hue + 20}, 80%, 35%, 1) 100%)`,
              boxShadow: `0 0 0 ${isListening ? 10 : 4}px hsla(${hue}, 85%, 60%, ${isListening ? 0.25 : 0.15}),
                          0 10px 30px hsla(${hue}, 80%, 30%, 0.5)`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'box-shadow 0.3s ease-out, transform 0.15s ease-out',
              transform: isListening ? 'scale(1.04)' : 'scale(1)',
              animation: isListening ? 'cvPulse 1.8s ease-in-out infinite' : 'none',
            }}
          >
            {/* Mic glyph */}
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none">
              <rect x="9" y="3" width="6" height="12" rx="3" fill="white" />
              <path
                d="M5 11a7 7 0 0 0 14 0"
                stroke="white"
                strokeWidth="2"
                strokeLinecap="round"
                fill="none"
              />
              <line x1="12" y1="18" x2="12" y2="21" stroke="white" strokeWidth="2" strokeLinecap="round" />
            </svg>
            {isProcessing && (
              <div
                style={{
                  position: 'absolute',
                  inset: -6,
                  borderRadius: '50%',
                  border: '2px solid rgba(255,255,255,0.4)',
                  borderTopColor: 'white',
                  animation: 'cvSpin 0.9s linear infinite',
                }}
              />
            )}
          </button>
        </div>
      </div>

      <style>{`
        @keyframes cvFadeIn {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes cvPulse {
          0%, 100% { box-shadow: 0 0 0 8px hsla(210, 85%, 60%, 0.2), 0 10px 30px hsla(210, 80%, 30%, 0.5); }
          50% { box-shadow: 0 0 0 16px hsla(210, 85%, 60%, 0.1), 0 10px 30px hsla(210, 80%, 30%, 0.5); }
        }
        @keyframes cvSpin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>,
    document.body,
  );
}
