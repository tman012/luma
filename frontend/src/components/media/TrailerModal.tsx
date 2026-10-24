import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';

interface TrailerModalProps {
  title: string;
  youtubeKey: string;
  onClose: () => void;
}

/**
 * Full-screen cinematic YouTube trailer overlay.
 *
 * - Stacks on top of the detail modal (z-[200])
 * - 100% black backdrop — nothing visible behind
 * - Close button only appears on mouse hover (alongside YouTube controls)
 * - Removes the iframe from the DOM on close to guarantee audio stops
 * - Esc or backdrop click closes
 */
export function TrailerModal({ title, youtubeKey, onClose }: TrailerModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    },
    [onClose],
  );

  // Trap Esc at the capture phase so it doesn't also close the parent modal
  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown, true);
    return () => document.removeEventListener('keydown', handleKeyDown, true);
  }, [handleKeyDown]);

  // Auto-focus the backdrop so keyboard events work immediately
  useEffect(() => {
    backdropRef.current?.focus();
  }, []);

  // Clear hide timer on unmount
  useEffect(() => {
    return () => { clearTimeout(hideTimerRef.current); };
  }, []);

  const handleMouseMove = () => {
    setHovered(true);
    clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => setHovered(false), 2500);
  };

  const handleMouseLeave = () => {
    clearTimeout(hideTimerRef.current);
    setHovered(false);
  };

  const embedUrl = `https://www.youtube.com/embed/${youtubeKey}?autoplay=1&rel=0&modestbranding=1&playsinline=1&controls=1`;

  return createPortal(
    <div className="luma-bleed"
      ref={backdropRef}
      tabIndex={-1}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 400,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#000',
        outline: 'none',
      }}
    >
      {/* Content card */}
      <div
        style={{
          width: '90vw',
          maxWidth: '1200px',
          position: 'relative',
        }}
      >
        {/* Close button — only visible on hover */}
        <button
          type="button"
          onClick={onClose}
          style={{
            position: 'absolute',
            top: '-48px',
            right: 0,
            zIndex: 10,
            background: 'rgba(255,255,255,0.1)',
            border: 'none',
            borderRadius: '50%',
            width: '40px',
            height: '40px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            color: '#fff',
            fontSize: '20px',
            opacity: hovered ? 1 : 0,
            transition: 'opacity 0.3s, background 0.2s',
            pointerEvents: hovered ? 'auto' : 'none',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background =
              'rgba(255,255,255,0.25)';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background =
              'rgba(255,255,255,0.1)';
          }}
          aria-label="Close trailer"
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        {/* 16:9 responsive player */}
        <div
          style={{
            position: 'relative',
            width: '100%',
            paddingBottom: '56.25%', // 16:9
            borderRadius: '12px',
            overflow: 'hidden',
            background: '#000',
          }}
        >
          <iframe
            src={embedUrl}
            title={`${title} — Trailer`}
            allow="autoplay; encrypted-media; picture-in-picture"
            allowFullScreen
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: '100%',
              border: 'none',
            }}
          />
        </div>
      </div>
    </div>,
    document.body,
  );
}
