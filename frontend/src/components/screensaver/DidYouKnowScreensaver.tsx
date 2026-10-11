import { useState, useEffect, useRef, useCallback } from 'react';
import '@/styles/trivia.css';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TriviaOption {
  text: string;
  isCorrect: boolean;
}

interface TriviaQuestion {
  question: string;
  options: TriviaOption[];
}

interface DidYouKnowScreensaverProps {
  enabled?: boolean;
  idleTimeoutMs?: number;
}

// ---------------------------------------------------------------------------
// HTML entity decoder
// ---------------------------------------------------------------------------

const ENTITY_MAP: Record<string, string> = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&#039;': "'",
  '&apos;': "'",
};

function decodeEntities(text: string): string {
  let decoded = text.replace(
    /&(?:amp|lt|gt|quot|apos|#039);/g,
    (m) => ENTITY_MAP[m] ?? m,
  );
  decoded = decoded.replace(/&#(\d+);/g, (_, n) =>
    String.fromCharCode(Number(n)),
  );
  decoded = decoded.replace(/&#x([0-9a-fA-F]+);/g, (_, h) =>
    String.fromCharCode(parseInt(h, 16)),
  );
  return decoded;
}

// ---------------------------------------------------------------------------
// Shuffle
// ---------------------------------------------------------------------------

function shuffleArray<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    const tmp = a[i]!;
    a[i] = a[j]!;
    a[j] = tmp;
  }
  return a;
}

// ---------------------------------------------------------------------------
// Trivia fetcher
// ---------------------------------------------------------------------------

const TRIVIA_URL =
  'https://opentdb.com/api.php?amount=10&category=11&type=multiple';

async function fetchTriviaBatch(): Promise<TriviaQuestion[]> {
  const res = await fetch(TRIVIA_URL);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  if (data.response_code !== 0 || !Array.isArray(data.results)) return [];

  return data.results.map(
    (r: {
      question: string;
      correct_answer: string;
      incorrect_answers: string[];
    }) => {
      const correct = decodeEntities(r.correct_answer);
      const incorrect = r.incorrect_answers.map(decodeEntities);
      const options: TriviaOption[] = shuffleArray([
        { text: correct, isCorrect: true },
        ...incorrect.map((t) => ({ text: t, isCorrect: false })),
      ]);
      return { question: decodeEntities(r.question), options };
    },
  );
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LABELS = ['A', 'B', 'C', 'D'];
const COUNTDOWN_SECONDS = 10;
const NEXT_QUESTION_SECONDS = 3;

// ---------------------------------------------------------------------------
// Dynamic font sizing — question scales per-text, answers use UNIFORM size
// ---------------------------------------------------------------------------

function questionFontSize(text: string): string {
  const len = text.length;
  if (len < 40) return 'clamp(3.6rem, 7vw, 7rem)';
  if (len < 80) return 'clamp(3rem, 5.5vw, 5.5rem)';
  if (len < 130) return 'clamp(2.4rem, 4.5vw, 4.5rem)';
  return 'clamp(2rem, 3.5vw, 3.5rem)';
}

/** Returns a single font size for ALL answers based on the longest one. */
function uniformAnswerFontSize(options: TriviaOption[]): string {
  const maxLen = Math.max(...options.map((o) => o.text.length));
  if (maxLen < 15) return 'clamp(2.8rem, 5vw, 5rem)';
  if (maxLen < 30) return 'clamp(2.4rem, 4vw, 4rem)';
  if (maxLen < 50) return 'clamp(2rem, 3.2vw, 3.2rem)';
  return 'clamp(1.6rem, 2.6vw, 2.6rem)';
}

function uniformLabelFontSize(options: TriviaOption[]): string {
  const maxLen = Math.max(...options.map((o) => o.text.length));
  if (maxLen < 15) return 'clamp(3rem, 5.2vw, 5rem)';
  if (maxLen < 30) return 'clamp(2.6rem, 4.2vw, 4.2rem)';
  if (maxLen < 50) return 'clamp(2.2rem, 3.4vw, 3.4rem)';
  return 'clamp(1.8rem, 2.8vw, 2.8rem)';
}

// ---------------------------------------------------------------------------
// Card styles — glassmorphism: dark glass base, green correct, dimmed wrong
// ---------------------------------------------------------------------------

type CardVariant = 'question' | 'answer' | 'correct' | 'dimmed';

function cardStyles(variant: CardVariant): React.CSSProperties {
  const base: React.CSSProperties = {
    borderRadius: '1.25rem',
    transition: 'background 0.5s ease, border-color 0.5s ease, box-shadow 0.5s ease, opacity 0.5s ease',
  };

  switch (variant) {
    case 'question':
      return {
        ...base,
        background: 'rgba(255,255,255,0.08)',
        backdropFilter: 'blur(24px)',
        WebkitBackdropFilter: 'blur(24px)',
        border: '1px solid rgba(255,255,255,0.12)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.40), 0 2px 8px rgba(0,0,0,0.20), inset 0 1px 0 rgba(255,255,255,0.10)',
      };
    case 'answer':
      return {
        ...base,
        background: 'rgba(255,255,255,0.06)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        border: '1px solid rgba(255,255,255,0.08)',
        boxShadow: '0 4px 20px rgba(0,0,0,0.35), 0 2px 8px rgba(0,0,0,0.15), inset 0 1px 0 rgba(255,255,255,0.06)',
        animation: 'trivia-pill-breathe 8s ease-in-out infinite',
      };
    case 'correct':
      return {
        ...base,
        background: 'rgba(34,197,94,0.15)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        border: '1px solid rgba(34,197,94,0.40)',
        animation: 'trivia-correct-reveal 0.6s ease-out forwards',
      };
    case 'dimmed':
      return {
        ...base,
        background: 'rgba(255,255,255,0.03)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        border: '1px solid rgba(255,255,255,0.04)',
        boxShadow: '0 2px 10px rgba(0,0,0,0.20), 0 1px 4px rgba(0,0,0,0.10)',
        opacity: 0.35,
      };
  }
}

// ---------------------------------------------------------------------------
// DarkCinemaBackground — deep dark base + ambient blobs + noise + embers
// ---------------------------------------------------------------------------

const EMBERS = Array.from({ length: 20 }, (_, i) => ({
  left: `${3 + Math.random() * 94}%`,
  size: 12 + Math.random() * 20,
  delay: Math.random() * 22,
  duration: 16 + Math.random() * 16,
  color: [
    'rgba(59,130,246,0.12)',
    'rgba(139,92,246,0.10)',
    'rgba(168,85,247,0.10)',
    'rgba(20,184,166,0.10)',
    'rgba(99,102,241,0.10)',
    'rgba(79,70,229,0.08)',
  ][i % 6],
}));

function DarkCinemaBackground() {
  return (
    <div className="absolute inset-0 overflow-hidden">
      {/* Solid dark base */}
      <div
        className="absolute inset-0"
        style={{ background: '#050507' }}
      />

      {/* Ambient color blobs with slow hue-rotate drift */}
      <div
        className="absolute inset-0"
        style={{ animation: 'trivia-ambient-shift 90s ease-in-out infinite' }}
      >
        {/* Deep purple */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(ellipse at 25% 20%, rgba(139,92,246,0.14) 0%, transparent 55%)',
            animation: 'trivia-blob-drift-1 22s ease-in-out infinite',
          }}
        />
        {/* Deep blue */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(ellipse at 70% 65%, rgba(59,130,246,0.12) 0%, transparent 50%)',
            animation: 'trivia-blob-drift-2 28s ease-in-out infinite',
          }}
        />
        {/* Deep rose */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(ellipse at 60% 30%, rgba(244,63,94,0.10) 0%, transparent 45%)',
            animation: 'trivia-blob-drift-3 24s ease-in-out infinite',
          }}
        />
        {/* Deep teal */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(ellipse at 15% 75%, rgba(20,184,166,0.18) 0%, transparent 50%)',
            animation: 'trivia-blob-drift-1 30s ease-in-out infinite reverse',
          }}
        />
      </div>

      {/* Theatrical vignette */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(ellipse at center, transparent 20%, rgba(0,0,0,0.7) 100%)',
        }}
      />

      {/* SVG noise texture — anti-banding */}
      <svg className="absolute inset-0 h-full w-full" style={{ opacity: 0.03 }}>
        <filter id="trivia-noise">
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.9"
            numOctaves="4"
            stitchTiles="stitch"
          />
        </filter>
        <rect width="100%" height="100%" filter="url(#trivia-noise)" />
      </svg>

      {/* Floating embers — dim jewel tones */}
      {EMBERS.map((b, i) => (
        <div
          key={i}
          className="absolute rounded-full"
          style={{
            left: b.left,
            bottom: '-40px',
            width: b.size,
            height: b.size,
            background: b.color,
            animation: `trivia-float-up ${b.duration}s ${b.delay}s linear infinite`,
          }}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DidYouKnowScreensaver({
  enabled = true,
  idleTimeoutMs = 120_000,
}: DidYouKnowScreensaverProps) {
  const [isIdle, setIsIdle] = useState(false);
  const [queue, setQueue] = useState<TriviaQuestion[]>([]);
  const [current, setCurrent] = useState<TriviaQuestion | null>(null);
  const [fadeIn, setFadeIn] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const [countdown, setCountdown] = useState(COUNTDOWN_SECONDS);
  const [nextCountdown, setNextCountdown] = useState(0);

  const lastActivityRef = useRef(Date.now());
  const idleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const nextTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fetchingRef = useRef(false);
  const queueRef = useRef(queue);
  queueRef.current = queue;

  // ---- Countdown helper -------------------------------------------------

  const clearCountdown = useCallback(() => {
    if (countdownRef.current) {
      clearInterval(countdownRef.current);
      countdownRef.current = null;
    }
    if (nextTimerRef.current) {
      clearInterval(nextTimerRef.current);
      nextTimerRef.current = null;
    }
  }, []);

  const startCountdown = useCallback(() => {
    clearCountdown();
    setRevealed(false);
    setCountdown(COUNTDOWN_SECONDS);

    let remaining = COUNTDOWN_SECONDS;
    countdownRef.current = setInterval(() => {
      remaining -= 1;
      setCountdown(remaining);
      if (remaining <= 0) {
        clearInterval(countdownRef.current!);
        countdownRef.current = null;
        setRevealed(true);
      }
    }, 1000);
  }, [clearCountdown]);

  // ---- Activity tracking ------------------------------------------------

  const recordActivity = useCallback(() => {
    lastActivityRef.current = Date.now();
    if (isIdle) {
      setIsIdle(false);
    }
  }, [isIdle]);

  useEffect(() => {
    if (!enabled) return;

    const check = () => {
      const elapsed = Date.now() - lastActivityRef.current;
      if (elapsed >= idleTimeoutMs) setIsIdle(true);
    };

    idleTimerRef.current = setInterval(check, 1000);

    const events = [
      'mousedown',
      'mousemove',
      'keydown',
      'touchstart',
      'scroll',
      'wheel',
    ] as const;
    const handler = () => {
      lastActivityRef.current = Date.now();
    };
    for (const e of events)
      window.addEventListener(e, handler, { passive: true });

    return () => {
      if (idleTimerRef.current) clearInterval(idleTimerRef.current);
      for (const e of events) window.removeEventListener(e, handler);
    };
  }, [enabled, idleTimeoutMs]);

  // Clean up all timers when leaving idle
  useEffect(() => {
    if (!isIdle) {
      clearCountdown();
    }
  }, [isIdle, clearCountdown]);

  // ---- Prefetch trivia --------------------------------------------------

  const fetchMore = useCallback(async () => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      const batch = await fetchTriviaBatch();
      if (batch.length > 0) setQueue((prev) => [...prev, ...batch]);
    } catch {
      /* keep cached */
    } finally {
      fetchingRef.current = false;
    }
  }, []);

  useEffect(() => {
    if (enabled) fetchMore();
  }, [enabled, fetchMore]);
  useEffect(() => {
    if (queue.length < 3) fetchMore();
  }, [queue.length, fetchMore]);

  // ---- Pick next question -----------------------------------------------

  const pickNext = useCallback(() => {
    setFadeIn(false);
    clearCountdown();
    setRevealed(false);
    setCountdown(COUNTDOWN_SECONDS);

    setTimeout(() => {
      setQueue((prev) => {
        if (prev.length === 0) {
          setCurrent(null);
          return prev;
        }
        setCurrent(prev[0] ?? null);
        return prev.slice(1);
      });
      requestAnimationFrame(() => {
        setFadeIn(true);
        startCountdown();
      });
    }, 500);
  }, [clearCountdown, startCountdown]);

  // ---- Show first question when idle starts ------------------------------

  useEffect(() => {
    if (!isIdle) return;

    if (!current && queueRef.current.length > 0) {
      setCurrent(queueRef.current[0] ?? null);
      setQueue(queueRef.current.slice(1));
      setRevealed(false);
      setCountdown(COUNTDOWN_SECONDS);
      setNextCountdown(0);
      requestAnimationFrame(() => {
        setFadeIn(true);
        startCountdown();
      });
    }
  }, [isIdle, startCountdown]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- "Next Question..." countdown after reveal -------------------------

  useEffect(() => {
    if (!revealed || !isIdle) return;

    setNextCountdown(NEXT_QUESTION_SECONDS);
    let remaining = NEXT_QUESTION_SECONDS;

    nextTimerRef.current = setInterval(() => {
      remaining -= 1;
      setNextCountdown(remaining);
      if (remaining <= 0) {
        clearInterval(nextTimerRef.current!);
        nextTimerRef.current = null;
        pickNext();
      }
    }, 1000);

    return () => {
      if (nextTimerRef.current) {
        clearInterval(nextTimerRef.current);
        nextTimerRef.current = null;
      }
    };
  }, [revealed, isIdle, pickNext]);

  // ---- Exit on interaction -----------------------------------------------

  useEffect(() => {
    if (!isIdle) return;
    const exit = () => recordActivity();
    const events = ['mousedown', 'touchstart', 'keydown'] as const;
    for (const e of events)
      window.addEventListener(e, exit, { once: true });
    return () => {
      for (const e of events) window.removeEventListener(e, exit);
    };
  }, [isIdle, recordActivity]);

  // ---- Render ------------------------------------------------------------

  if (!enabled || !isIdle) return null;

  // Pre-compute uniform sizes for current question's answers
  const answerSize = current ? uniformAnswerFontSize(current.options) : '1rem';
  const labelSize = current ? uniformLabelFontSize(current.options) : '1rem';

  return (
    <div className="fixed inset-0 z-[99999] flex flex-col items-center overflow-hidden">
      <DarkCinemaBackground />

      {/* Main content layer — vertically centered, leaves room for countdown */}
      <div className="relative z-10 flex w-full flex-1 flex-col items-center justify-center px-8 pb-40 sm:px-16 sm:pb-48 lg:px-24">
        {/* ---- Fade wrapper ---- */}
        <div
          className="flex w-full flex-col items-center"
          style={{
            opacity: fadeIn ? 1 : 0,
            transform: fadeIn ? 'translateY(0) scale(1)' : 'translateY(12px) scale(0.97)',
            transition: 'opacity 0.6s ease, transform 0.6s ease',
          }}
        >
          {current ? (
            <>
              {/* ---- Question card ---- */}
              <div
                className="mb-8 w-full sm:mb-10"
                style={cardStyles('question')}
              >
                <p
                  className="w-full px-10 py-7 text-center leading-snug font-bold sm:px-20 sm:py-10"
                  style={{
                    color: 'rgba(255,255,255,0.95)',
                    fontSize: questionFontSize(current.question),
                    textShadow: '0 2px 20px rgba(0,0,0,0.5)',
                    overflowWrap: 'break-word',
                    wordBreak: 'break-word',
                  }}
                >
                  {current.question}
                </p>
              </div>

              {/* ---- Answer grid 2x2 — equal-height rows ---- */}
              <div className="grid w-full grid-cols-1 gap-5 sm:grid-cols-2 sm:gap-x-8 sm:gap-y-6">
                {current.options.map((opt, i) => {
                  const isCorrectRevealed = revealed && opt.isCorrect;
                  const isDimmed = revealed && !opt.isCorrect;

                  let variant: CardVariant = 'answer';
                  if (isCorrectRevealed) variant = 'correct';
                  else if (isDimmed) variant = 'dimmed';

                  return (
                    <div
                      key={`${current.question}-${i}`}
                      className="flex min-h-0 items-center"
                      style={{
                        ...cardStyles(variant),
                        animationDelay: variant === 'answer' ? `${i * -2}s` : undefined,
                      }}
                    >
                      <span
                        className="ml-8 mr-3 flex-shrink-0 sm:ml-10 sm:mr-4"
                        style={{
                          fontSize: labelSize,
                          fontWeight: 800,
                          color: isCorrectRevealed ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.55)',
                          textShadow: isCorrectRevealed
                            ? '0 0 12px rgba(255,255,255,0.5)'
                            : '0 1px 3px rgba(0,0,0,0.3)',
                          transition: 'text-shadow 0.5s ease, color 0.5s ease',
                        }}
                      >
                        {LABELS[i]}.
                      </span>
                      <span
                        className="py-5 pr-8 leading-snug font-semibold sm:py-7 sm:pr-10"
                        style={{
                          fontSize: answerSize,
                          color: isCorrectRevealed ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.85)',
                          fontWeight: isCorrectRevealed ? 800 : 600,
                          textShadow: isCorrectRevealed
                            ? '0 0 12px rgba(255,255,255,0.5)'
                            : '0 2px 12px rgba(0,0,0,0.4)',
                          transition: 'text-shadow 0.5s ease, color 0.5s ease',
                          overflowWrap: 'break-word',
                          wordBreak: 'break-word',
                        }}
                      >
                        {opt.text}
                      </span>
                    </div>
                  );
                })}
              </div>
            </>
          ) : (
            <p
              className="py-20 text-5xl font-bold"
              style={{ color: 'rgba(255,255,255,0.15)' }}
            >
              Loading trivia&hellip;
            </p>
          )}
        </div>
      </div>

      {/* ---- Countdown timer — pinned to bottom center, always visible ---- */}
      {current && !revealed && countdown > 0 && (
        <div
          key={countdown}
          className="absolute bottom-6 left-0 right-0 z-10 select-none text-center sm:bottom-10"
          style={{
            fontSize: 'clamp(6rem, 12vw, 12rem)',
            fontWeight: 900,
            fontVariantNumeric: 'tabular-nums',
            color: countdown <= 3 ? 'rgba(245,158,11,0.70)' : 'rgba(255,255,255,0.45)',
            textShadow: countdown <= 3
              ? '0 0 40px rgba(245,158,11,0.40), 0 0 80px rgba(245,158,11,0.15), 0 4px 20px rgba(0,0,0,0.3)'
              : '0 0 30px rgba(255,255,255,0.12), 0 0 60px rgba(59,130,246,0.08), 0 4px 20px rgba(0,0,0,0.3)',
            animation: 'trivia-countdown-pulse 1s ease-in-out forwards',
            transition: 'color 0.5s ease, text-shadow 0.5s ease',
          }}
        >
          {countdown}
        </div>
      )}

      {/* ---- "Next Question..." — bottom right after reveal ---- */}
      {revealed && nextCountdown > 0 && (
        <div
          className="absolute right-8 bottom-8 z-10 select-none sm:right-12 sm:bottom-12"
          style={{
            fontSize: 'clamp(1.4rem, 2vw, 2rem)',
            fontWeight: 600,
            color: 'rgba(255,255,255,0.35)',
            textShadow: '0 2px 12px rgba(0,0,0,0.4)',
            transition: 'opacity 0.4s ease',
          }}
        >
          Next Question...{' '}
          <span
            style={{
              fontVariantNumeric: 'tabular-nums',
              fontWeight: 800,
            }}
          >
            {nextCountdown}
          </span>
        </div>
      )}
    </div>
  );
}
