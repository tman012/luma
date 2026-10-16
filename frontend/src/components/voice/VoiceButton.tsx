interface VoiceButtonProps {
  onClick: () => void;
  size?: 'sm' | 'md';
  className?: string;
}

export function VoiceButton({ onClick, size = 'md', className = '' }: VoiceButtonProps) {
  const iconSize = size === 'sm' ? 20 : 28;
  const btnSize = size === 'sm' ? 'h-10 w-10' : 'min-h-[64px] min-w-[64px]';

  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center justify-center rounded-full text-white/60 transition-colors hover:bg-white/10 hover:text-white/85 active:bg-white/15 ${btnSize} ${className}`}
      aria-label="Voice assistant"
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width={iconSize}
        height={iconSize}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
        <line x1="12" y1="19" x2="12" y2="22" />
      </svg>
    </button>
  );
}
