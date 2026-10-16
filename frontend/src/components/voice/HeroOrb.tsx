import { useState, useEffect, useRef, useMemo } from 'react';
import { useVoice } from '@/contexts/VoiceContext';

const EMBER_FAMILY = "'Amazon Ember', -apple-system, BlinkMacSystemFont, sans-serif";

/** Helper quote templates — {name} is replaced with the capitalized wake word */
const QUOTE_TEMPLATES = [
  "{name}, show me Avatar",
  "{name}, do I have Inception in 4K?",
  "{name}, find me something like Interstellar",
  "{name}, what's that movie with the spinning top?",
  "{name}, show me what's trending",
  "{name}, turn on the family room lights",
  "{name}, add Dune to my watchlist",
  "{name}, what time is it?",
  "{name}, play The Office",
  "{name}, go to my watchlist",
  "{name}, describe a thriller set in space",
  "{name}, do I have Breaking Bad?",
];

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

export function HeroOrb() {
  const { voiceEnabled, wakeWord } = useVoice();
  const [quoteIndex, setQuoteIndex] = useState(0);
  const [fade, setFade] = useState(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const quotes = useMemo(
    () => QUOTE_TEMPLATES.map((t) => t.replace('{name}', capitalize(wakeWord))),
    [wakeWord],
  );

  // Cycle quotes every 8 seconds with fade transition
  useEffect(() => {
    intervalRef.current = setInterval(() => {
      setFade(false);
      setTimeout(() => {
        setQuoteIndex((i) => (i + 1) % quotes.length);
        setFade(true);
      }, 400);
    }, 8000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [quotes.length]);

  if (!voiceEnabled) return null;

  return (
    <p
      className="absolute inset-x-0 z-30"
      style={{
        fontFamily: EMBER_FAMILY,
        fontSize: 24,
        fontWeight: 300,
        fontStyle: 'italic',
        color: 'rgba(255,255,255,0.55)',
        letterSpacing: '0.02em',
        textAlign: 'center',
        bottom: 28,
        opacity: fade ? 1 : 0,
        transition: 'opacity 0.4s ease',
        userSelect: 'none',
      }}
    >
      Try saying &ldquo;{quotes[quoteIndex]}&rdquo;
    </p>
  );
}
