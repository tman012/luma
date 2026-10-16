import { IconButton } from './IconButton';

interface BackButtonProps {
  onClick: () => void;
  className?: string;
  ariaLabel?: string;
  size?: number;
  strokeWidth?: number;
}

export function BackButton({
  onClick,
  className,
  ariaLabel = 'Back',
  size = 20,
  strokeWidth = 2,
}: BackButtonProps) {
  return (
    <IconButton ariaLabel={ariaLabel} onClick={onClick} className={className}>
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M15 18l-6-6 6-6" />
      </svg>
    </IconButton>
  );
}
