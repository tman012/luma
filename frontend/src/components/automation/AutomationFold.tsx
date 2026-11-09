/** Collapses an automation section's details. The header controls stay outside this button. */
import type { MouseEvent, ReactNode } from 'react';

const CONTROL = 'button, a, input, select, textarea, label, [role="switch"], [role="checkbox"], [role="listbox"], [role="option"]';

/** A click on the header bar toggles only when it did not land on a control. */
export function foldClickToggles(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.closest !== 'function') return false;
  return !el.closest(CONTROL);
}

export function onFoldBarClick(e: MouseEvent, toggle: () => void) {
  if (foldClickToggles(e.target)) toggle();
}

export function FoldTitle({
  id, controls, open, onToggle, children,
}: {
  id: string;
  controls: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      id={id}
      className="gr-fold"
      data-testid={id}
      aria-expanded={open}
      aria-controls={controls}
      aria-labelledby={`${id}-title`}
      onClick={(e) => { e.stopPropagation(); onToggle(); }}
    >
      <span className="gr-fold-text">{children}</span>
      <svg className="gr-chevron" aria-hidden="true" width="18" height="18" viewBox="0 0 18 18" style={{ transform: open ? 'rotate(180deg)' : 'none' }}>
        <path d="M4.5 6.5 L9 11 L13.5 6.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}

export function FoldDetails({ id, labelledBy, open, children }: { id: string; labelledBy: string; open: boolean; children: ReactNode }) {
  return (
    <div id={id} role="region" aria-labelledby={labelledBy} hidden={!open}>
      {children}
    </div>
  );
}
