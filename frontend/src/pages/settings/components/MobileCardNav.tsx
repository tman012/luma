/**
 * MobileCardNav — Spotify-style list → detail navigation for settings cards on mobile.
 *
 * On mobile: shows a list of items. Tapping drills into that card.
 * Uses CardNavContext (provided by SettingsPage) so the page header
 * updates to show the card title and handles back navigation.
 * On desktop: renders all content stacked (no list).
 */

import { createContext, useContext, type ReactNode } from 'react';
import { useUiDensity } from '@/hooks/useUiDensity';
import { cn } from '@/utils/cn';

export interface CardItem {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  content: ReactNode;
}

// ── Context for page-level card navigation ────────────────────────

interface CardNavContextValue {
  activeCard: number | null;
  onDrill: (index: number, title: string) => void;
}

export const CardNavContext = createContext<CardNavContextValue>({
  activeCard: null,
  onDrill: () => {},
});

export function useCardNav() {
  return useContext(CardNavContext);
}

// ── Component ─────────────────────────────────────────────────────

interface MobileCardNavProps {
  items: CardItem[];
}

export function MobileCardNav({ items }: MobileCardNavProps) {
  const d = useUiDensity();
  const isMobile = d.mode === 'compact';
  const { activeCard, onDrill } = useCardNav();

  // On desktop, render all content stacked
  if (!isMobile) {
    return (
      <>
        {items.map((item, i) => (
          <div key={i}>{item.content}</div>
        ))}
      </>
    );
  }

  // ── Mobile: drilled into a card — just render the content ──
  if (activeCard !== null && items[activeCard]) {
    return <div>{items[activeCard].content}</div>;
  }

  // ── Mobile: card list ──
  return (
    <div>
      {items.map((item, i) => (
        <button
          key={i}
          type="button"
          onClick={() => onDrill(i, item.title)}
          className={cn(
            'flex w-full items-center gap-4 px-1 py-4 min-h-[60px] text-left transition-colors active:bg-white/[0.04]',
            i > 0 && 'border-t border-white/[0.04]',
          )}
        >
          {item.icon ? (
            <span className="text-white/50 shrink-0">{item.icon}</span>
          ) : (
            <span className="w-[22px] shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            <p className="text-[16px] font-medium text-white/90 leading-snug">{item.title}</p>
          </div>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white/20 shrink-0">
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
      ))}
    </div>
  );
}
