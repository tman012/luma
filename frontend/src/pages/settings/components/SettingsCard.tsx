/**
 * SettingsCard — glass card wrapper with icon, title, optional test button, and save/reset actions.
 * v174 (Designer): no subtitle or note is shown (no helper text); the props stay for callers.
 */

import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { useSettings } from '../useSettings';
import { TestButton } from './TestButton';
import { HeaderSwitch } from './ToggleSwitch';
import { cn } from '@/utils/cn';
import { PRIMARY_BTN, SECONDARY_BTN } from './legacyTokens';
import { PHONE_CARD_QUERY, useMediaQuery } from './useMediaQuery';

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
  /** v175 (Designer): the card's on/off path when its only toggle would repeat the title: the switch
   * sits right-aligned in the header (after headerRight), named by the title. */
  headerToggle?: string;
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
  headerToggle,
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
  const phone = useMediaQuery(PHONE_CARD_QUERY);
  // v175 (Designer): on a phone the action sits left of Reset and Save, or full width above them when
  // the row can't hold all three (its natural width is remembered while it isn't stretched).
  const actionsRef = useRef<HTMLDivElement>(null);
  const actionRef = useRef<HTMLDivElement>(null);
  const natural = useRef(0);
  const [actionFull, setActionFull] = useState(false);
  useLayoutEffect(() => {
    if (!phone || !headerRight) return;
    const row = actionsRef.current;
    const act = actionRef.current;
    if (!row || !act) return;
    const measure = () => {
      if (!act.className.includes('basis-full')) natural.current = act.offsetWidth;
      const gap = parseFloat(getComputedStyle(row).columnGap || '0') || 0;
      const rest = [...row.children].filter(c => c !== act).reduce((w, c) => w + (c as HTMLElement).offsetWidth + gap, 0);
      const need = natural.current + rest;
      setActionFull(natural.current > 0 && need > row.clientWidth + 0.5);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(row);
    return () => ro.disconnect();
  }, [phone, headerRight]);
  return (
    <div
      data-testid="settings-card"
      className={cn('rounded-[16px] border border-white/[0.08] bg-white/[0.06] p-5', className)}
    >
      {/* Header. v175 (Designer): under 600px the icon is hidden and the action button moves to the
          footer, so every phone header is the title left and the switch right on one line. */}
      <div className="mb-4 flex items-center justify-between gap-4" data-testid="settings-card-header">
        <div className="flex items-center gap-3 min-w-0">
          {icon && !phone && (
            <div className="flex h-[36px] w-[36px] items-center justify-center rounded-[10px] bg-white/[0.06] text-white/60 shrink-0">
              {icon}
            </div>
          )}
          <h3 className={cn('text-[15px] font-semibold text-white/95', phone && 'min-w-0 truncate')}>{title}</h3>
        </div>
        {((headerRight && !phone) || headerToggle) && (
          <div className="flex shrink-0 items-center gap-3">
            {!phone && headerRight}
            {headerToggle && <HeaderSwitch label={title} path={headerToggle} />}
          </div>
        )}
      </div>

      {/* Fields */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {children}
      </div>

      {/* Actions: right-aligned at the end of the content, not pinned */}
      <div ref={actionsRef} className="mt-5 flex flex-wrap items-center justify-end gap-2.5" data-testid="settings-card-actions">
        {(testService || onTest) && (
          <div className="mr-auto"><TestButton label={testLabel} onTest={handleTest} /></div>
        )}
        {phone && headerRight && (
          <div ref={actionRef} data-testid="settings-card-footer-action"
            className={cn('mr-auto', actionFull && 'basis-full [&>button]:w-full')}>
            {headerRight}
          </div>
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
