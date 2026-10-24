/**
 * PageModal — wraps page content in a modal overlay matching MovieDetailModal sizing.
 * Clicking outside the card or pressing Escape navigates to home.
 */

import { useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router';
import { NAVY_GLASS_BORDER, NAVY_GLASS_PANEL } from '@/theme/tokens';

interface Props {
  children: ReactNode;
  /** v174 (Designer, legacy-settings-modal-fix.md): the legacy Settings shell. A navy scrim
   * rgba(21,16,42,.72) with an 8px blur; the shell rgba(21,16,42,.92) with a 40px blur, a 1px white
   * 8% border and a 20px radius. No black. */
  variant?: 'default' | 'settings';
}

export const SETTINGS_SCRIM = 'rgba(21,16,42,0.72)';
export const SETTINGS_SHELL = 'rgba(21,16,42,0.92)';
export const SETTINGS_SHELL_BORDER = '1px solid rgba(255,255,255,0.08)';

type Phase = 'entering' | 'visible' | 'exiting' | 'gone';

export function PageModal({ children, variant = 'default' }: Props) {
  const settings = variant === 'settings';
  const [phase, setPhase] = useState<Phase>('entering');
  const overlayRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

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
      navigate('/');
    }, 300);
  }, [navigate]);

  // Escape key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [handleClose]);

  if (phase === 'gone') return null;

  const overlayOpacity = phase === 'visible' ? 1 : 0;
  const cardScale = phase === 'visible' ? 'scale(1)' : 'scale(0.96)';
  const cardOpacity = phase === 'visible' ? 1 : 0;

  return createPortal(
    <div className="luma-bleed"
      data-modal="PageModal"
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
        background: settings ? SETTINGS_SCRIM : 'rgba(0,0,0,0.7)',
        ...(settings ? { backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)' } : null),
        opacity: overlayOpacity,
        transition: 'opacity 0.3s ease',
        padding: '20px',
      }}
    >
      {/* Card — same dimensions as MovieDetailModal */}
      <div
        data-testid="page-modal-card"
        data-variant={variant}
        style={{
          position: 'relative',
          width: '100%',
          maxWidth: '1500px',
          height: '90vh',
          borderRadius: '20px',
          overflow: 'hidden',
          background: settings ? SETTINGS_SHELL : NAVY_GLASS_PANEL,
          border: settings ? SETTINGS_SHELL_BORDER : NAVY_GLASS_BORDER,
          boxShadow: settings ? '0 32px 100px rgba(21,16,42,0.6)' : '0 32px 100px rgba(0,0,0,0.8)',
          ...(settings ? { backdropFilter: 'blur(40px)', WebkitBackdropFilter: 'blur(40px)' } : null),
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
        <div data-scroll-container className="luma-scroll-y" style={{ height: '100%', overflowY: 'auto', WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}>
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}
