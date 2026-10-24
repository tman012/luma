import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router';
import { cn } from '@/utils/cn';
import { availableModes, fetchVibeSettings } from '@/services/vibeApi';

const DISMISS_THRESHOLD = 120;
const ENTER_DURATION = 600;
const EXIT_DURATION = 400;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';

const MODE_CARDS = [
  {
    id: 'feel',
    emoji: '\u{1F3AD}',
    title: 'Feel something',
    subtitle: 'Browse by mood',
    gradient: 'from-purple-600/40 to-indigo-900/40',
    border: 'border-purple-500/30',
  },
  {
    id: 'pick',
    emoji: '\u{1F3B2}',
    title: 'Pick for me',
    subtitle: 'Spin the roulette',
    gradient: 'from-amber-600/40 to-red-900/40',
    border: 'border-amber-500/30',
  },
  {
    id: 'describe',
    emoji: '\u{2728}',
    title: 'Describe it',
    subtitle: 'Tell me what you want',
    gradient: 'from-rose-600/40 to-pink-900/40',
    border: 'border-rose-500/30',
  },
];

interface LumaAISheetProps {
  open: boolean;
  onClose: () => void;
}

export function LumaAISheet({ open, onClose }: LumaAISheetProps) {
  const navigate = useNavigate();
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting' | 'gone'>('gone');
  const [aiSearch, setAiSearch] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    fetchVibeSettings().then((s) => { if (alive) setAiSearch(s.aiSearchAvailable); }).catch(() => {});
    return () => { alive = false; };
  }, [open]);

  useEffect(() => {
    if (open) {
      setPhase('entering');
      setDragOffset(0);
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setPhase('visible'));
      });
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const prevBg = document.documentElement.style.background;
    const prevOverflow = document.body.style.overflow;
    document.documentElement.style.background = '#000';
    document.body.style.overflow = 'hidden';
    return () => {
      document.documentElement.style.background = prevBg;
      document.body.style.overflow = prevOverflow;
    };
  }, [open]);

  const handleDismiss = useCallback(() => {
    setDragOffset(0);
    requestAnimationFrame(() => {
      setPhase('exiting');
      setTimeout(() => { setPhase('gone'); onClose(); }, EXIT_DURATION);
    });
  }, [onClose]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-sheet-handle]') && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
    }
  }, []);
  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || !e.touches.length) return;
    dragRef.current.currentY = e.touches[0]!.clientY;
    setDragOffset(Math.max(0, dragRef.current.currentY - dragRef.current.startY));
  }, []);
  const onTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) handleDismiss();
    else setDragOffset(0);
  }, [handleDismiss]);

  const handleCardClick = (modeId: string) => {
    handleDismiss();
    setTimeout(() => {
      if (modeId === 'describe') {
        navigate('/describe-it');
      } else {
        navigate(`/library?mode=${modeId}`);
      }
    }, EXIT_DURATION);
  };

  if (phase === 'gone' && !open) return null;

  const sheetTransform =
    dragOffset > 0 ? `translate3d(0, ${dragOffset}px, 0)`
      : phase === 'visible' ? 'translate3d(0, 0, 0)'
        : 'translate3d(0, 100%, 0)';

  const sheetTransition =
    dragOffset > 0 ? 'none'
      : phase === 'exiting' ? `transform ${EXIT_DURATION}ms cubic-bezier(0.4, 0, 1, 1)`
        : `transform ${ENTER_DURATION}ms ${SPRING}`;

  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      data-sheet="LumaAISheet"
      style={{
        position: 'fixed', inset: 0,
        zIndex: 10001,
        display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
        backgroundColor: phase === 'visible' ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0)',
        transition: phase === 'exiting'
          ? `background-color ${EXIT_DURATION}ms ease`
          : `background-color ${ENTER_DURATION}ms ease`,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) handleDismiss(); }}
    >
      <div className="luma-bleed-panel"
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          height: 'calc(100% - env(safe-area-inset-top, 0px) - 6px)',
          display: 'flex', flexDirection: 'column',
          background: 'var(--color-kiosk-black)',
          borderRadius: '20px 20px 0 0',
          overflow: 'hidden',
          transform: sheetTransform,
          transition: sheetTransition,
          willChange: 'transform',
          backfaceVisibility: 'hidden',
        }}
      >
        {/* Grab handle */}
        <div data-sheet-handle
          style={{ flexShrink: 0, display: 'flex', justifyContent: 'center', padding: '12px 0 8px', cursor: 'grab' }}
        >
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>

        {/* Content — vertically centered */}
        <div
          data-scroll-container
          className="luma-scroll-y flex-1 flex flex-col items-center justify-center px-4 pb-8"
          style={{ WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}
        >
          <h2 className="mb-10 text-[36px] font-bold tracking-tight text-white text-center">Luma AI</h2>

          <div className="flex w-full max-w-[400px] flex-col gap-4">
            {availableModes(MODE_CARDS, aiSearch).map((card, idx) => (
              <button
                key={card.id}
                type="button"
                onClick={() => handleCardClick(card.id)}
                className={cn(
                  'group relative flex items-center gap-5 h-[88px] rounded-2xl border backdrop-blur-md',
                  'bg-gradient-to-br transition-all duration-300 px-5',
                  'active:scale-[0.98]',
                  card.gradient,
                  card.border,
                )}
                style={{
                  opacity: phase === 'visible' ? 1 : 0,
                  transform: phase === 'visible' ? 'translateY(0)' : 'translateY(20px)',
                  transition: `opacity 0.4s ${0.1 + idx * 0.08}s ease-out, transform 0.4s ${0.1 + idx * 0.08}s ease-out`,
                }}
              >
                <span className="text-[40px] transition-transform duration-300 group-hover:scale-110">
                  {card.emoji}
                </span>
                <div className="flex flex-col items-start">
                  <span className="text-[20px] font-semibold text-white">
                    {card.title}
                  </span>
                  <span className="text-[14px] text-white/50">
                    {card.subtitle}
                  </span>
                </div>
              </button>
            ))}
          </div>

          <div className="shrink-0" style={{ height: 'env(safe-area-inset-bottom, 12px)' }} />
        </div>
      </div>
    </div>,
    document.body,
  );
}
