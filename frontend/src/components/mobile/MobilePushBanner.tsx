import { usePushSubscription } from '@/hooks/usePushSubscription';

export function MobilePushBanner() {
  const { showPrompt, subscribe, dismiss, state } = usePushSubscription();

  if (!showPrompt) return null;

  return (
    <div className="mx-4 mb-3 rounded-2xl bg-blue-600/20 border border-blue-500/30 p-4 flex items-center gap-3"
         style={{ backdropFilter: 'blur(12px)' }}>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-white/90">
          Get notified when new movies and shows are added
        </p>
      </div>
      <button
        onClick={subscribe}
        disabled={state === 'loading'}
        className="shrink-0 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white
                   active:bg-blue-700 disabled:opacity-50 transition-colors"
      >
        {state === 'loading' ? 'Enabling...' : 'Enable'}
      </button>
      <button
        onClick={dismiss}
        className="shrink-0 p-1 text-white/40 active:text-white/70 transition-colors"
        aria-label="Dismiss"
      >
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path d="M6 6l8 8M14 6l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}
