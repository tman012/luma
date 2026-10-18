/**
 * SettingsCard — glass card wrapper with icon, title, optional test button, and save/reset actions.
 * v174 (Designer): no subtitle or note is shown (no helper text); the props stay for callers.
 */

import { useCallback, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { useSettings } from '../useSettings';
import { TestButton } from './TestButton';
import { cn } from '@/utils/cn';
import { PRIMARY_BTN, SECONDARY_BTN } from './legacyTokens';

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

  void subtitle; void note;            // v174 (Designer): no helper text
  return (
    <div
      data-testid="settings-card"
      className={cn('rounded-[16px] border border-white/[0.08] bg-white/[0.06] p-5', className)}
    >
      {/* Header */}
      <div className="mb-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          {icon && (
            <div className="flex h-[36px] w-[36px] items-center justify-center rounded-[10px] bg-white/[0.06] text-white/60 shrink-0">
              {icon}
            </div>
          )}
          <h3 className="text-[15px] font-semibold text-white/95">{title}</h3>
        </div>
        {headerRight}
      </div>

      {/* Fields */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {children}
      </div>

      {/* Actions: right-aligned at the end of the content, not pinned */}
      <div className="mt-5 flex flex-wrap items-center justify-end gap-2.5" data-testid="settings-card-actions">
        {(testService || onTest) && (
          <div className="mr-auto"><TestButton label={testLabel} onTest={handleTest} /></div>
        )}
        <button type="button" onClick={handleReset} disabled={!dirty} className={SECONDARY_BTN}>
          Reset
        </button>
        {signIn ? (
          <SignInToSave />
        ) : (
          <button type="button" onClick={handleSave} disabled={!dirty} className={PRIMARY_BTN} data-testid="settings-card-save">
            Save
          </button>
        )}
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
      className={PRIMARY_BTN}
    >
      {SIGN_IN_TO_SAVE}
    </button>
  );
}
