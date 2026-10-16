/**
 * Explore Visually — floating poster field.
 *
 * Posters drift slowly across the screen. Drag to pan.
 * Tap a poster → zoom → open shared modal.
 *
 * Performance: limits visible posters to ~30, recycles off-screen ones.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import type { VibeItem } from '@/types/vibe';

interface Props {
  library: VibeItem[];
  onOpenModal: (item: VibeItem) => void;
}

interface FloatingPoster {
  item: VibeItem;
  x: number;
  y: number;
  vx: number;
  vy: number;
  scale: number;
  rotation: number;
  opacity: number;
  zIndex: number;
}

const MAX_POSTERS = 30;
const POSTER_W = 160;
const POSTER_H = 240;
const DRIFT_SPEED = 0.15; // pixels per frame

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

function createPoster(item: VibeItem, viewW: number, viewH: number): FloatingPoster {
  const scale = 0.7 + Math.random() * 0.6; // 0.7–1.3
  const angle = Math.random() * Math.PI * 2;
  return {
    item,
    x: Math.random() * (viewW + 400) - 200,
    y: Math.random() * (viewH + 400) - 200,
    vx: Math.cos(angle) * DRIFT_SPEED * (0.5 + Math.random()),
    vy: Math.sin(angle) * DRIFT_SPEED * (0.5 + Math.random()),
    scale,
    rotation: -8 + Math.random() * 16, // -8 to +8 degrees
    opacity: 0.6 + Math.random() * 0.4,
    zIndex: Math.floor(scale * 10),
  };
}

export function ExploreVisuallyMode({ library, onOpenModal }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const postersRef = useRef<FloatingPoster[]>([]);
  const rafRef = useRef<number>(0);
  const [, forceRender] = useState(0);
  const [tappedIdx, setTappedIdx] = useState<number | null>(null);

  // Drag/pan state
  const panRef = useRef({ offsetX: 0, offsetY: 0 });
  const dragRef = useRef({ active: false, startX: 0, startY: 0, startOX: 0, startOY: 0, moved: false });

  // Initialize posters
  const eligibleItems = useMemo(
    () => shuffleArray(library.filter((i) => i.posterUrl)).slice(0, MAX_POSTERS),
    [library],
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    postersRef.current = eligibleItems.map((item) =>
      createPoster(item, rect.width, rect.height),
    );
    forceRender((n) => n + 1);
  }, [eligibleItems]);

  // Animation loop
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let running = true;
    const animate = () => {
      if (!running) return;
      const rect = el.getBoundingClientRect();
      const w = rect.width + 400;
      const h = rect.height + 400;

      for (const p of postersRef.current) {
        p.x += p.vx;
        p.y += p.vy;

        // Wrap around edges
        if (p.x > w - 200) p.x = -POSTER_W * p.scale - 200;
        if (p.x < -POSTER_W * p.scale - 200) p.x = w - 200;
        if (p.y > h - 200) p.y = -POSTER_H * p.scale - 200;
        if (p.y < -POSTER_H * p.scale - 200) p.y = h - 200;
      }

      forceRender((n) => n + 1);
      rafRef.current = requestAnimationFrame(animate);
    };

    rafRef.current = requestAnimationFrame(animate);
    return () => {
      running = false;
      cancelAnimationFrame(rafRef.current);
    };
  }, [eligibleItems]);

  // Drag handlers (pointer events for touch + mouse)
  const onPointerDown = useCallback((e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('[data-poster]')) return;
    dragRef.current = {
      active: true,
      startX: e.clientX,
      startY: e.clientY,
      startOX: panRef.current.offsetX,
      startOY: panRef.current.offsetY,
      moved: false,
    };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragRef.current.active) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    if (Math.abs(dx) > 5 || Math.abs(dy) > 5) dragRef.current.moved = true;
    panRef.current.offsetX = dragRef.current.startOX + dx;
    panRef.current.offsetY = dragRef.current.startOY + dy;
  }, []);

  const onPointerUp = useCallback(() => {
    dragRef.current.active = false;
  }, []);

  // Tap poster → zoom reveal → modal
  const handlePosterTap = useCallback(
    (idx: number) => {
      if (dragRef.current.moved) {
        dragRef.current.moved = false;
        return;
      }
      setTappedIdx(idx);
      // Open modal after zoom animation
      setTimeout(() => {
        const poster = postersRef.current[idx];
        if (poster) onOpenModal(poster.item);
        setTappedIdx(null);
      }, 400);
    },
    [onOpenModal],
  );

  const posters = postersRef.current;
  const ox = panRef.current.offsetX;
  const oy = panRef.current.offsetY;

  return (
    <div className="relative z-10 h-full">
      <style>{`
        @keyframes posterZoom {
          0% { transform: var(--poster-transform); }
          100% { transform: translate(-50%, -50%) scale(1.8); z-index: 100; }
        }
      `}</style>

      {/* Title overlay */}
      <div className="pointer-events-none absolute inset-x-0 top-6 z-40 text-center">
        <h2 className="text-[clamp(24px,3vw,40px)] font-bold text-white/80 drop-shadow-lg">
          Drag to explore &middot; Tap to open
        </h2>
      </div>

      {/* Poster field */}
      <div
        ref={containerRef}
        className="h-full w-full cursor-grab overflow-hidden active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        style={{ touchAction: 'none' }}
      >
        {posters.map((p, idx) => {
          const isTapped = tappedIdx === idx;
          const tx = p.x + ox;
          const ty = p.y + oy;
          const transform = `translate(${tx}px, ${ty}px) scale(${p.scale}) rotate(${p.rotation}deg)`;

          return (
            <div
              key={p.item.ratingKey}
              data-poster
              className="absolute left-0 top-0 transition-shadow duration-300 hover:shadow-[0_0_30px_rgba(255,255,255,0.2)]"
              style={{
                width: POSTER_W,
                height: POSTER_H,
                transform: isTapped ? `translate(${tx}px, ${ty}px) scale(${p.scale * 1.5})` : transform,
                zIndex: isTapped ? 100 : p.zIndex,
                opacity: isTapped ? 1 : p.opacity,
                transition: isTapped ? 'transform 0.4s cubic-bezier(0.34,1.56,0.64,1), opacity 0.3s' : 'none',
                willChange: 'transform',
                ['--poster-transform' as string]: transform,
              }}
            >
              <button
                type="button"
                onClick={() => handlePosterTap(idx)}
                className="h-full w-full overflow-hidden rounded-xl outline-none ring-0 focus-visible:ring-2 focus-visible:ring-white/40"
              >
                <img
                  src={p.item.posterUrl ?? ''}
                  alt={p.item.title}
                  loading="lazy"
                  className="pointer-events-none h-full w-full object-cover"
                  draggable={false}
                />
              </button>

              {/* Title tooltip on hover */}
              <div className="pointer-events-none absolute -bottom-7 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-black/80 px-3 py-1 text-[12px] text-white opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100">
                {p.item.title}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
