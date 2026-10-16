import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';
import { useUiDensity } from '@/hooks/useUiDensity';
import { uiDensityTokens } from '@/ui/uiDensityTokens';

interface IconButtonProps {
  ariaLabel: string;
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
  title?: string;
  children: ReactNode;
}

export function IconButton({
  ariaLabel,
  onClick,
  disabled,
  className,
  title,
  children,
}: IconButtonProps) {
  const d = useUiDensity();
  const tk = uiDensityTokens(d);

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      title={title}
      className={cn(
        'inline-flex items-center justify-center select-none',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60',
        tk.targets.minBtn,
        className,
      )}
    >
      {children}
    </button>
  );
}
