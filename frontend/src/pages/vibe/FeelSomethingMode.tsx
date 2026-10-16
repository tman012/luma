/**
 * Feel Something — mood-cluster browsing.
 *
 * Shows 8 vibe cards. Tap a vibe → shelf of posters.
 * Tap a poster → open shared modal.
 */

import { useState, useMemo, useRef, useCallback } from 'react';
import { getVibeShelves, VIBE_DEFINITIONS } from '@/services/vibeEngine';
import { cn } from '@/utils/cn';
import { rtCriticIcon } from '@/utils/ratingIcons';
import type { VibeItem, VibeShelf, VibeId } from '@/types/vibe';

interface Props {
  library: VibeItem[];
  onOpenModal: (item: VibeItem) => void;
  initialVibe?: VibeId | null;
}

export function FeelSomethingMode({ library, onOpenModal, initialVibe }: Props) {
  const [activeVibe, setActiveVibe] = useState<VibeId | null>(initialVibe ?? null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dragState = useRef({ down: false, startX: 0, scrollLeft: 0, moved: false });

  const shelves = useMemo(() => getVibeShelves(library), [library]);
  const shelvesMap = useMemo(() => {
    const map: Record<string, VibeShelf> = {};
    for (const s of shelves) map[s.vibe.id] = s;
    return map;
  }, [shelves]);

  const activeShelf = activeVibe ? shelvesMap[activeVibe] : null;

  // Drag-to-scroll handlers
  const onMouseDown = useCallback((e: React.MouseEvent) => {
    const el = scrollRef.current;
    if (!el) return;
    dragState.current = { down: true, startX: e.pageX, scrollLeft: el.scrollLeft, moved: false };
    el.style.cursor = 'grabbing';
  }, []);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    const ds = dragState.current;
    if (!ds.down) return;
    e.preventDefault();
    const dx = e.pageX - ds.startX;
    if (Math.abs(dx) > 5) ds.moved = true;
    scrollRef.current!.scrollLeft = ds.scrollLeft - dx;
  }, []);

  const onMouseUp = useCallback(() => {
    dragState.current.down = false;
    if (scrollRef.current) scrollRef.current.style.cursor = 'grab';
  }, []);

  return (
    <div className="relative z-10 flex h-full flex-col">
      <style>{`
        @keyframes vibeCardIn {
          0% { opacity: 0; transform: scale(0.85) translateY(30px); }
          100% { opacity: 1; transform: scale(1) translateY(0); }
        }
        @keyframes shelfSlide {
          0% { opacity: 0; transform: translateY(40px); }
          100% { opacity: 1; transform: translateY(0); }
        }
      `}</style>

      {/* Header */}
      <div className="shrink-0 px-5 pt-16 pb-3 text-center sm:px-10 sm:pt-8 sm:pb-4">
        <h2 className="text-[24px] font-bold text-white sm:text-[clamp(32px,3.5vw,56px)]">
          {activeVibe ? (shelvesMap[activeVibe]?.vibe.emoji + ' ' + shelvesMap[activeVibe]?.vibe.name) : 'How do you want to feel?'}
        </h2>
        {activeVibe && (
          <button
            type="button"
            onClick={() => setActiveVibe(null)}
            className="mt-2 rounded-full bg-white/10 px-5 py-1.5 text-[14px] text-white/70 backdrop-blur-sm transition-colors hover:bg-white/20 sm:mt-3 sm:px-6 sm:py-2 sm:text-[16px]"
          >
            All Vibes
          </button>
        )}
      </div>

      {/* Vibe grid (when no vibe selected) */}
      {!activeVibe && (
        <div className="flex flex-1 items-center justify-center overflow-y-auto px-4 sm:px-10">
          <div className="grid grid-cols-2 gap-3 max-w-[1200px] sm:grid-cols-4 sm:gap-5">
            {VIBE_DEFINITIONS.map((vibe, idx) => {
              const shelf = shelvesMap[vibe.id];
              const count = shelf?.items.length ?? 0;
              return (
                <button
                  key={vibe.id}
                  type="button"
                  onClick={() => count > 0 && setActiveVibe(vibe.id)}
                  disabled={count === 0}
                  className={cn(
                    'group relative flex flex-col items-center justify-center gap-2 sm:gap-3',
                    'h-[130px] sm:h-[200px] rounded-xl sm:rounded-2xl border backdrop-blur-md',
                    'transition-all duration-300',
                    count > 0
                      ? 'hover:scale-[1.05] hover:shadow-xl active:scale-[0.97] cursor-pointer border-white/15 bg-white/5'
                      : 'opacity-30 cursor-not-allowed border-white/5 bg-white/[0.02]',
                  )}
                  style={{
                    animation: `vibeCardIn 0.4s ${idx * 0.06}s cubic-bezier(0.34, 1.56, 0.64, 1) both`,
                  }}
                >
                  {/* Color accent line */}
                  <div
                    className="absolute inset-x-4 top-0 h-[2px] rounded-full opacity-60 sm:inset-x-6 sm:h-[3px]"
                    style={{ backgroundColor: vibe.color }}
                  />
                  <span className="text-[32px] sm:text-[48px] transition-transform duration-300 group-hover:scale-110">
                    {vibe.emoji}
                  </span>
                  <span className="text-[15px] sm:text-[20px] font-semibold text-white">
                    {vibe.name}
                  </span>
                  <span className="text-[12px] sm:text-[14px] text-white/40">
                    {count > 0 ? `${count} titles` : 'No matches'}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Shelf view (when a vibe is selected) */}
      {activeVibe && activeShelf && (
        <div
          className="flex-1 overflow-hidden"
          style={{ animation: 'shelfSlide 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) both' }}
        >
          <div
            ref={scrollRef}
            className="flex h-full items-center gap-3 overflow-x-auto px-4 pb-6 sm:gap-5 sm:px-10 sm:pb-8"
            style={{ scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch', cursor: 'grab' }}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={onMouseUp}
            onMouseLeave={onMouseUp}
          >
            {activeShelf.items.map((item) => (
              <button
                key={item.ratingKey}
                type="button"
                onClick={() => {
                  if (!dragState.current.moved) onOpenModal(item);
                  dragState.current.moved = false;
                }}
                className="group relative shrink-0 text-left outline-none transition-transform duration-300 hover:scale-105 focus-visible:scale-105"
              >
                <div className="relative w-[160px] overflow-hidden rounded-lg sm:w-[220px] sm:rounded-xl">
                  <div className="aspect-[2/3]">
                    {item.posterUrl ? (
                      <img
                        src={item.posterUrl}
                        alt={item.title}
                        loading="lazy"
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <div className="flex h-full w-full items-center justify-center bg-white/5 text-sm text-white/30">
                        No Poster
                      </div>
                    )}
                  </div>

                  {/* Hover overlay with info */}
                  <div className="absolute inset-0 flex flex-col justify-end bg-gradient-to-t from-black/80 via-black/20 to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100 p-3 sm:p-4">
                    <p className="text-[12px] text-white/70 sm:text-[13px]">
                      {item.year ?? ''}{item.runtimeMinutes > 0 ? ` \u{00B7} ${item.runtimeMinutes}m` : ''}
                    </p>
                  </div>

                  {/* Watch state dot */}
                  {item.viewCount > 0 && (
                    <div className="absolute right-2 top-2 h-2.5 w-2.5 rounded-full bg-green-400/80 shadow-lg sm:h-3 sm:w-3" />
                  )}

                  {/* RT critic score badge */}
                  {item.rtCriticScore != null && (
                    <div className="absolute bottom-2 left-2 flex items-center gap-1 rounded-full bg-black/70 px-2 py-0.5 backdrop-blur-sm">
                      <img src={rtCriticIcon(item.rtCriticScore)} alt="RT" className="h-3.5 w-auto" />
                      <span className="text-[11px] font-bold text-white">{item.rtCriticScore}%</span>
                    </div>
                  )}
                </div>

                <div className="mt-1.5 w-[160px] px-0.5 sm:mt-2 sm:w-[220px] sm:px-1">
                  <p className="truncate text-[13px] font-medium text-white sm:text-[15px]">
                    {item.title}
                  </p>
                  <p className="truncate text-[11px] text-white/40 sm:text-[13px]">
                    {item.genres.slice(0, 2).join(' \u{00B7} ')}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
