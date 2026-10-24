/**
 * VibeEngineModal — renders the Vibe Engine inside a modal overlay
 * with the same dimming/card treatment as MovieDetailModal.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { VibeEnginePage } from '@/pages/VibeEnginePage';
import { useLayoutMode } from '@/contexts/LayoutModeContext';

interface Props {
  onClose: () => void;
}

type Phase = 'entering' | 'visible' | 'exiting' | 'gone';

export function VibeEngineModal({ onClose }: Props) {
  const [phase, setPhase] = useState<Phase>('entering');
  const overlayRef = useRef<HTMLDivElement>(null);
  const { mode } = useLayoutMode();
  const isMobile = mode === 'compact';

  // Animate open
  useEffect(() => {
    const frame = requestAnimationFrame(() => setPhase('visible'));
    return () => cancelAnimationFrame(frame);
  }, []);

  // Body scroll lock
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  const handleClose = useCallback(() => {
    setPhase('exiting');
    setTimeout(() => {
      setPhase('gone');
      onClose();
    }, 300);
  }, [onClose]);

  // Escape key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [handleClose]);

  if (phase === 'gone') return null;

  // Mobile: skip the card wrapper — VibeEnginePage renders its own mobile sheet overlay.
  // This avoids creating route-based navigation, preventing the iOS PWA swipe-back flash.
  if (isMobile) {
    return createPortal(
      <VibeEnginePage isModal onClose={onClose} />,
      document.body,
    );
  }

  const overlayOpacity = phase === 'visible' ? 1 : 0;
  const cardScale = phase === 'entering' ? 'scale(0.96)' : phase === 'exiting' ? 'scale(0.96)' : 'scale(1)';
  const cardOpacity = phase === 'visible' ? 1 : 0;

  return createPortal(
    <div className="luma-bleed"
      data-modal="VibeEngineModal"
      ref={overlayRef}
      onClick={(e) => {
        if (e.target === overlayRef.current) handleClose();
      }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0,0,0,0.7)',
        opacity: overlayOpacity,
        transition: 'opacity 0.3s ease',
        padding: '20px',
      }}
    >
      {/* Card — same dimensions as MovieDetailModal */}
      <div
        style={{
          position: 'relative',
          width: '100%',
          maxWidth: '1500px',
          height: '90vh',
          borderRadius: '20px',
          overflow: 'hidden',
          backgroundColor: '#1e1e1e',
          boxShadow: '0 32px 100px rgba(0,0,0,0.8)',
          transform: cardScale,
          opacity: cardOpacity,
          transition:
            phase === 'exiting'
              ? 'transform 0.3s ease, opacity 0.3s ease'
              : 'transform 0.5s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.5s cubic-bezier(0.16, 1, 0.3, 1)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <VibeEnginePage isModal onClose={handleClose} />
      </div>
    </div>,
    document.body,
  );
}
