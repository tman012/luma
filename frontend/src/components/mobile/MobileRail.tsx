import { staggerMs } from '@/theme/motion';
import { Children, cloneElement, isValidElement, type ReactNode, type ReactElement } from 'react';

interface MobileRailProps {
  title: string;
  children: ReactNode;
  /** Rail index used for staggered entrance animation delay */
  index?: number;
}

export function MobileRail({ title, children, index = 0 }: MobileRailProps) {
  // Inject margin on first/last children since padding on overflow-x containers is unreliable
  const items = Children.toArray(children).filter(isValidElement);
  const GAP = 12; // matches gap-3
  const railBaseDelay = staggerMs(index);

  return (
    <section className="mb-6">
      {/* Rail title fades in */}
      <h2
        className="mobile-rail-title-enter text-lg font-semibold mb-2 text-white"
        style={{
          paddingLeft: GAP,
          opacity: 0,
          animation: `mobilePageEnter var(--m-dur-emphasized) var(--m-ease-decel) ${railBaseDelay}ms forwards`,
        }}
      >
        {title}
      </h2>
      <div
        className="flex items-start gap-3 overflow-x-auto snap-x snap-mandatory pb-2"
        style={{ scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch' }}
      >
        {items.map((child, i) => {
          const style: Record<string, unknown> = {};
          if (i === 0) style.marginLeft = GAP;
          if (i === items.length - 1) style.marginRight = GAP;
          // Per-card stagger: the rail's delay plus the shared stagger step per card
          const cardDelay = railBaseDelay + staggerMs(i);
          style.opacity = 0;
          style.animation = `mobileCardEnter var(--m-dur-emphasized) var(--m-ease-decel) ${cardDelay}ms forwards`;
          return cloneElement(child as ReactElement<{ style?: Record<string, unknown>; className?: string }>, {
            style: { ...(child as ReactElement<{ style?: Record<string, unknown> }>).props.style, ...style },
            className: `mobile-card-enter ${(child as ReactElement<{ className?: string }>).props.className ?? ''}`,
          });
        })}
      </div>
    </section>
  );
}
