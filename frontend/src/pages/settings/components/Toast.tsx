/**
 * Toast — notification container that renders toasts from SettingsContext.
 */

import { useSettings } from '../useSettings';
import { cn } from '@/utils/cn';

export function ToastContainer() {
  const { toasts, dismissToast } = useSettings();

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-6 right-6 z-[9999] flex flex-col gap-2">
      {toasts.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => dismissToast(t.id)}
          className={cn(
            'flex items-center gap-3 rounded-xl px-4 py-3 text-[13px] font-medium shadow-lg backdrop-blur-md transition-all',
            'animate-[toastIn_0.25s_ease-out]',
            t.type === 'success' && 'bg-green-500/20 text-green-400 border border-green-500/20',
            t.type === 'error' && 'bg-red-500/20 text-red-400 border border-red-500/20',
            t.type === 'info' && 'bg-white/10 text-white/80 border border-white/10',
          )}
        >
          {t.type === 'success' && (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          )}
          {t.type === 'error' && (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" /><line x1="15" y1="9" x2="9" y2="15" /><line x1="9" y1="9" x2="15" y2="15" />
            </svg>
          )}
          {t.type === 'info' && (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" />
            </svg>
          )}
          {t.message}
        </button>
      ))}
    </div>
  );
}
