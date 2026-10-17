/**
 * SettingsCard — glass card wrapper with icon, title, subtitle,
 * optional test button, and save/reset actions.
 */

import { useCallback, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { useSettings } from '../useSettings';
import { TestButton } from './TestButton';
import { cn } from '@/utils/cn';

interface SettingsCardProps {
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  /** Dot-paths owned by this card (for save/reset/dirty) */
  paths: string[];
  /** Service name for test button (e.g. 'plex', 'sonarr') */
  testService?: string;
  /** Custom test handler (overrides default testConnection) */
  onTest?: () => Promise<{ success: boolean; message: string }>;
  /** Test button label override */
  testLabel?: string;
  /** Extra content in header-right (e.g. custom buttons) */
  headerRight?: ReactNode;
  /** Footer note */
  note?: string;
  children: ReactNode;
  className?: string;
}

export function SettingsCard({
  title,
  subtitle,
  icon,
  paths,
  testService,
  onTest,
  testLabel,
  headerRight,
  note,
  children,
  className,
}: SettingsCardProps) {
  const { saveCard, resetCard, isCardDirty, needsSignIn, testConnection } = useSettings();

  const dirty = isCardDirty(paths);
  // Turning on or widening automatic deletion needs the owner signed in with
  // a session; the server answers 401 and Save becomes a sign-in.
  const signIn = dirty && !!needsSignIn?.(paths);

  const handleSave = useCallback(() => saveCard(paths), [saveCard, paths]);
  const handleReset = useCallback(() => resetCard(paths), [resetCard, paths]);

  const handleTest = useCallback(async () => {
    if (onTest) return onTest();
    if (testService) return testConnection(testService);
    return { success: false, message: 'No test configured' };
  }, [onTest, testService, testConnection]);

  return (
    <div
      className={cn(
        'rounded-2xl border border-white/10 bg-white/[0.04] p-4 sm:p-6',
        className,
      )}
    >
      {/* Header */}
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          {icon && (
            <div className="flex h-[38px] w-[38px] sm:h-[42px] sm:w-[42px] items-center justify-center rounded-[10px] bg-white/5 text-white/50 shrink-0">
              {icon}
            </div>
          )}
          <div>
            <h3 className="text-[15px] font-semibold text-white/90">{title}</h3>
            {subtitle && (
              <p className="mt-0.5 text-[12px] text-white/35">{subtitle}</p>
            )}
          </div>
        </div>
        {headerRight}
      </div>

      {/* Fields */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {children}
      </div>

      {/* Note */}
      {note && (
        <p className="mt-4 text-[12px] text-white/40">{note}</p>
      )}

      {/* Actions */}
      <div className="mt-4 sm:mt-6 flex flex-col sm:flex-row sm:flex-wrap sm:items-center sm:justify-between gap-2 sm:gap-3 border-t border-white/[0.06] pt-4 sm:pt-5">
        <div className="flex gap-2.5">
          {(testService || onTest) && (
            <TestButton label={testLabel} onTest={handleTest} />
          )}
        </div>
        <div className="flex gap-2.5">
          <button
            type="button"
            onClick={handleReset}
            disabled={!dirty}
            className="flex-1 sm:flex-none inline-flex items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-4 py-2.5 text-[13px] font-semibold text-white/60 transition-colors hover:bg-white/10 hover:text-white/80 disabled:opacity-30 disabled:cursor-not-allowed min-h-[44px]"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
            </svg>
            Reset
          </button>
          {signIn ? (
            <SignInToSave />
          ) : (
            <button
              type="button"
              onClick={handleSave}
              disabled={!dirty}
              className="flex-1 sm:flex-none inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-500 px-4 py-2.5 text-[13px] font-semibold text-white transition-colors hover:bg-emerald-600 disabled:opacity-40 disabled:cursor-not-allowed min-h-[44px]"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" /><polyline points="17 21 17 13 7 13 7 21" /><polyline points="7 3 7 8 15 8" />
              </svg>
              Save
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Label of the sign-in prompt on Save (sentence case, no helper text). */
export const SIGN_IN_TO_SAVE = 'Sign in to save';

/** Replaces Save when the server wants the owner signed in for this change.
 * Its own component, so cards outside a router never call useNavigate. */
function SignInToSave() {
  const navigate = useNavigate();
  const location = useLocation();
  return (
    <button
      type="button"
      data-testid="settings-sign-in-to-save"
      onClick={() => navigate('/login', { state: { from: location.pathname + location.search, signIn: true } })}
      className="flex-1 sm:flex-none inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-500 px-4 py-2.5 text-[13px] font-semibold text-white transition-colors hover:bg-emerald-600 min-h-[44px]"
    >
      {SIGN_IN_TO_SAVE}
    </button>
  );
}
