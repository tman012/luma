import { useState, useEffect, useRef, useCallback } from 'react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface KioskScreensaverProps {
  enabled?: boolean;
  idleTimeoutMs?: number;
}

interface DiscoveryTile {
  id: string;
  title: string;
  descriptor: string;
  accentHue: number;
  mood: string;
}

// ---------------------------------------------------------------------------
// Discovery tile definitions — cinematic accent hues, no icons
// ---------------------------------------------------------------------------

const TILES: DiscoveryTile[] = [
  {
    id: 'trending',
    title: 'Trending now',
    descriptor: 'What everyone is watching',
    accentHue: 0,
    mood: 'urgent heat',
  },
  {
    id: 'new',
    title: 'New & buzzing',
    descriptor: 'Fresh releases making noise',
    accentHue: 45,
    mood: 'golden spotlight',
  },
  {
    id: 'surprise',
    title: 'Surprise me',
    descriptor: 'Something unexpected',
    accentHue: 270,
    mood: 'mysterious twilight',
  },
  {
    id: 'movie-night',
    title: 'Movie night picks',
    descriptor: 'Perfect for the whole group',
    accentHue: 220,
    mood: 'cool theater',
  },
  {
    id: 'vibe',
    title: 'Browse by vibe',
    descriptor: 'Match your mood',
    accentHue: 175,
    mood: 'oceanic calm',
  },
  {
    id: 'owned',
    title: 'We own this',
    descriptor: 'Your personal library',
    accentHue: 145,
    mood: 'verdant archive',
  },
  {
    id: 'watchlist',
    title: 'Watchlist',
    descriptor: 'Saved for later',
    accentHue: 25,
    mood: 'warm ember',
  },
  {
    id: 'continue',
    title: 'Continue watching',
    descriptor: 'Pick up where you left off',
    accentHue: 330,
    mood: 'intimate rose',
  },
];

// ---------------------------------------------------------------------------
// Ambient backdrop keyframes (CSS injected once)
// ---------------------------------------------------------------------------

const BACKDROP_STYLE_ID = 'kiosk-ss-backdrop-style';

function ensureBackdropStyles() {
  if (document.getElementById(BACKDROP_STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = BACKDROP_STYLE_ID;
  style.textContent = `
    @keyframes kiosk-ss-drift {
      0%   { transform: scale(1.08) translate(0%, 0%); }
      25%  { transform: scale(1.10) translate(-1%, 0.5%); }
      50%  { transform: scale(1.09) translate(0.5%, -1%); }
      75%  { transform: scale(1.11) translate(1%, 0.5%); }
      100% { transform: scale(1.08) translate(0%, 0%); }
    }
    @keyframes kiosk-ss-fade-in {
      from { opacity: 0; transform: translateY(12px) scale(0.98); }
      to   { opacity: 1; transform: translateY(0) scale(1); }
    }
    @keyframes kiosk-ss-ambient-shift {
      0%   { opacity: 0.08; filter: blur(80px) hue-rotate(0deg); }
      33%  { opacity: 0.12; filter: blur(85px) hue-rotate(15deg); }
      66%  { opacity: 0.06; filter: blur(80px) hue-rotate(-10deg); }
      100% { opacity: 0.08; filter: blur(80px) hue-rotate(0deg); }
    }
  `;
  document.head.appendChild(style);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function KioskScreensaver({
  enabled = true,
  idleTimeoutMs = 120_000,
}: KioskScreensaverProps) {
  const [isIdle, setIsIdle] = useState(false);
  const [visible, setVisible] = useState(false);
  const lastActivityRef = useRef(Date.now());
  const idleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---- Activity tracking ------------------------------------------------

  const recordActivity = useCallback(() => {
    lastActivityRef.current = Date.now();
    if (isIdle) {
      setIsIdle(false);
      setVisible(false);
    }
  }, [isIdle]);

  useEffect(() => {
    if (!enabled) return;

    const check = () => {
      const elapsed = Date.now() - lastActivityRef.current;
      if (elapsed >= idleTimeoutMs) setIsIdle(true);
    };

    idleTimerRef.current = setInterval(check, 1000);

    const events = ['mousedown', 'mousemove', 'keydown', 'touchstart', 'scroll', 'wheel'] as const;
    const handler = () => { lastActivityRef.current = Date.now(); };
    for (const e of events) window.addEventListener(e, handler, { passive: true });

    return () => {
      if (idleTimerRef.current) clearInterval(idleTimerRef.current);
      for (const e of events) window.removeEventListener(e, handler);
    };
  }, [enabled, idleTimeoutMs]);

  // ---- Inject backdrop keyframe styles ----------------------------------

  useEffect(() => {
    if (isIdle) ensureBackdropStyles();
  }, [isIdle]);

  // ---- Stagger fade-in after idle triggers ------------------------------

  useEffect(() => {
    if (!isIdle) return;
    const t = setTimeout(() => setVisible(true), 100);
    return () => clearTimeout(t);
  }, [isIdle]);

  // ---- Exit on interaction -----------------------------------------------

  useEffect(() => {
    if (!isIdle) return;
    const exit = () => recordActivity();
    const events = ['mousedown', 'touchstart', 'keydown'] as const;
    for (const e of events) window.addEventListener(e, exit, { once: true });
    return () => {
      for (const e of events) window.removeEventListener(e, exit);
    };
  }, [isIdle, recordActivity]);

  // ---- Render ------------------------------------------------------------

  if (!enabled || !isIdle) return null;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 99999,
        overflow: 'hidden',
        background: '#0a0a0a',
      }}
    >
      {/* ---- Ambient backdrop with Ken Burns drift ---- */}
      <div
        style={{
          position: 'absolute',
          inset: '-12%',
          background:
            'radial-gradient(ellipse at 25% 35%, rgba(30,20,80,0.18) 0%, transparent 55%), '
            + 'radial-gradient(ellipse at 75% 55%, rgba(15,40,90,0.15) 0%, transparent 50%), '
            + 'radial-gradient(ellipse at 50% 80%, rgba(80,25,40,0.10) 0%, transparent 45%), '
            + 'radial-gradient(ellipse at 60% 20%, rgba(20,30,60,0.08) 0%, transparent 40%)',
          animation: 'kiosk-ss-drift 60s ease-in-out infinite, kiosk-ss-ambient-shift 90s ease-in-out infinite',
        }}
      />

      {/* ---- Vignette overlay (stronger, more cinematic) ---- */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: 'radial-gradient(ellipse at center, transparent 20%, rgba(0,0,0,0.7) 100%)',
          pointerEvents: 'none',
        }}
      />

      {/* ---- Noise texture SVG filter (anti-banding) ---- */}
      <svg style={{ position: 'absolute', width: 0, height: 0 }} aria-hidden="true">
        <defs>
          <filter id="kiosk-ss-noise">
            <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="4" stitchTiles="stitch" />
          </filter>
        </defs>
      </svg>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: 0.03,
          filter: 'url(#kiosk-ss-noise)',
          pointerEvents: 'none',
        }}
      />

      {/* ---- Main content ---- */}
      <div
        style={{
          position: 'relative',
          zIndex: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          padding: 'clamp(24px, 4vh, 48px) clamp(24px, 5vw, 80px)',
          opacity: visible ? 1 : 0,
          transition: 'opacity 0.8s ease',
        }}
      >
        {/* ---- Title ---- */}
        <div
          style={{
            marginBottom: 'clamp(24px, 3vh, 48px)',
            textAlign: 'center',
            animation: visible ? 'kiosk-ss-fade-in 0.8s ease forwards' : undefined,
          }}
        >
          <h1
            style={{
              fontSize: 'clamp(2.2rem, 4.5vw, 3.8rem)',
              fontWeight: 600,
              letterSpacing: '-0.03em',
              color: 'rgba(255,255,255,0.85)',
              textShadow: '0 2px 30px rgba(0,0,0,0.6)',
              margin: 0,
            }}
          >
            What are you in the mood for?
          </h1>
          <p
            style={{
              fontSize: 'clamp(0.7rem, 1.2vw, 0.95rem)',
              color: 'rgba(255,255,255,0.25)',
              marginTop: '10px',
              fontWeight: 300,
            }}
          >
            Tap to explore
          </p>
        </div>

        {/* ---- Discovery tile grid (4x2) ---- */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 'clamp(14px, 1.8vw, 28px)',
            width: '100%',
            maxWidth: '1500px',
          }}
        >
          {TILES.map((tile, i) => (
            <CinematicCard key={tile.id} tile={tile} index={i} visible={visible} />
          ))}
        </div>

        {/* ---- Clock ---- */}
        <KioskClock visible={visible} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cinematic Discovery Card
// ---------------------------------------------------------------------------

function CinematicCard({
  tile,
  index,
  visible,
}: {
  tile: DiscoveryTile;
  index: number;
  visible: boolean;
}) {
  const handleMouseEnter = useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
    const el = e.currentTarget;
    el.style.transform = 'translateY(-6px) scale(1.03)';
    el.style.borderColor = 'rgba(255,255,255,0.18)';
    el.style.boxShadow = [
      `0 8px 32px hsla(${tile.accentHue}, 60%, 40%, 0.25)`,
      '0 4px 16px rgba(0,0,0,0.4)',
      `inset 0 1px 0 rgba(255,255,255,0.08)`,
    ].join(', ');
    el.style.transition = 'transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.4s cubic-bezier(0.34, 1.56, 0.64, 1), border-color 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)';
  }, [tile.accentHue]);

  const handleMouseLeave = useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
    const el = e.currentTarget;
    el.style.transform = '';
    el.style.boxShadow = '';
    el.style.borderColor = 'rgba(255,255,255,0.08)';
    el.style.transition = 'transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease';
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
    const el = e.currentTarget;
    el.style.transform = 'scale(0.97)';
    el.style.boxShadow = `inset 0 2px 8px rgba(0,0,0,0.3)`;
    el.style.transition = 'transform 0.15s ease, box-shadow 0.15s ease';
  }, []);

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
    const el = e.currentTarget;
    el.style.transform = 'translateY(-6px) scale(1.03)';
    el.style.boxShadow = [
      `0 8px 32px hsla(${tile.accentHue}, 60%, 40%, 0.25)`,
      '0 4px 16px rgba(0,0,0,0.4)',
      `inset 0 1px 0 rgba(255,255,255,0.08)`,
    ].join(', ');
    el.style.transition = 'transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)';
  }, [tile.accentHue]);

  // Poster strip gradient bands — each tile gets slightly shifted tones
  // derived from accentHue for a whisper of color in the poster zone
  const posterStripBg = [
    // 3 vertical bands simulating poster thumbnails
    `linear-gradient(180deg, rgba(255,255,255,0.02) 0%, rgba(255,255,255,0.04) 85%, transparent 100%)`,
    // Faint divider lines between "poster" areas
    `repeating-linear-gradient(90deg, transparent 0%, transparent 32%, rgba(255,255,255,0.03) 32%, rgba(255,255,255,0.03) 32.5%, transparent 32.5%, transparent 65%, rgba(255,255,255,0.03) 65%, rgba(255,255,255,0.03) 65.5%, transparent 65.5%)`,
    // Whisper of accent color in the poster zone
    `linear-gradient(180deg, hsla(${tile.accentHue}, 40%, 30%, 0.05) 0%, transparent 70%)`,
    // Bottom fade out of poster zone into text area
    `linear-gradient(180deg, transparent 60%, rgba(0,0,0,0.3) 100%)`,
  ].join(', ');

  return (
    <button
      type="button"
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
        padding: 0,
        borderRadius: '18px',
        border: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(255,255,255,0.04)',
        cursor: 'pointer',
        overflow: 'hidden',
        textAlign: 'left',
        outline: 'none',
        transition: 'transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease',
        animation: visible
          ? `kiosk-ss-fade-in 0.6s ease ${0.1 + index * 0.08}s both`
          : undefined,
        aspectRatio: '4 / 3',
        minHeight: 0,
        boxShadow: [
          'inset 0 1px 0 rgba(255,255,255,0.04)',
          '0 2px 8px rgba(0,0,0,0.3)',
          '0 8px 24px rgba(0,0,0,0.15)',
        ].join(', '),
      }}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onMouseDown={handleMouseDown}
      onMouseUp={handleMouseUp}
    >
      {/* ---- Film-grain noise overlay on card ---- */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: 0.04,
          filter: 'url(#kiosk-ss-noise)',
          borderRadius: 'inherit',
          pointerEvents: 'none',
          zIndex: 1,
        }}
      />

      {/* ---- Poster strip zone (top ~40%) ---- */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: '42%',
          background: posterStripBg,
          borderRadius: '18px 18px 0 0',
          pointerEvents: 'none',
          zIndex: 1,
        }}
      />

      {/* ---- Accent glow — bottom edge bleed ---- */}
      <div
        style={{
          position: 'absolute',
          bottom: '-30%',
          left: '10%',
          right: '10%',
          height: '60%',
          background: `radial-gradient(ellipse at center bottom, hsla(${tile.accentHue}, 55%, 45%, 0.12) 0%, transparent 70%)`,
          pointerEvents: 'none',
          zIndex: 0,
        }}
      />

      {/* ---- Subtle corner accent bleed ---- */}
      <div
        style={{
          position: 'absolute',
          bottom: 0,
          right: 0,
          width: '50%',
          height: '40%',
          background: `radial-gradient(ellipse at 100% 100%, hsla(${tile.accentHue}, 50%, 40%, 0.08) 0%, transparent 70%)`,
          pointerEvents: 'none',
          zIndex: 0,
        }}
      />

      {/* ---- Inner highlight edge (top) ---- */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: '1px',
          background: 'linear-gradient(90deg, transparent 10%, rgba(255,255,255,0.06) 50%, transparent 90%)',
          pointerEvents: 'none',
          zIndex: 2,
        }}
      />

      {/* ---- Text content (bottom-aligned) ---- */}
      <div
        style={{
          position: 'relative',
          zIndex: 3,
          padding: 'clamp(14px, 2vw, 24px)',
          paddingTop: 0,
        }}
      >
        <h2
          style={{
            fontSize: 'clamp(1.1rem, 2vw, 1.6rem)',
            fontWeight: 700,
            color: '#fff',
            margin: 0,
            lineHeight: 1.15,
            textShadow: '0 2px 12px rgba(0,0,0,0.6), 0 1px 3px rgba(0,0,0,0.4)',
            letterSpacing: '-0.01em',
          }}
        >
          {tile.title}
        </h2>
        <p
          style={{
            fontSize: 'clamp(0.75rem, 1vw, 0.9rem)',
            fontWeight: 400,
            color: 'rgba(255,255,255,0.4)',
            margin: 0,
            marginTop: 'clamp(4px, 0.5vh, 8px)',
            lineHeight: 1.3,
            textShadow: '0 1px 4px rgba(0,0,0,0.3)',
          }}
        >
          {tile.descriptor}
        </p>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Clock sub-component — updates every minute
// ---------------------------------------------------------------------------

function KioskClock({ visible }: { visible: boolean }) {
  const [time, setTime] = useState(() => formatTime());

  useEffect(() => {
    const t = setInterval(() => setTime(formatTime()), 30_000);
    return () => clearInterval(t);
  }, []);

  return (
    <p
      style={{
        marginTop: 'clamp(20px, 3vh, 40px)',
        fontSize: 'clamp(1.1rem, 1.8vw, 1.5rem)',
        fontWeight: 200,
        color: 'rgba(255,255,255,0.2)',
        letterSpacing: '0.12em',
        fontVariantNumeric: 'tabular-nums',
        animation: visible ? 'kiosk-ss-fade-in 0.8s ease 1s both' : undefined,
      }}
    >
      {time}
    </p>
  );
}

function formatTime(): string {
  const now = new Date();
  return now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}
