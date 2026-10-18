/**
 * ToggleSwitch — boolean toggle bound to a settings dot-path.
 */

import { useCallback } from 'react';
import { useSettings } from '../useSettings';
import { cn } from '@/utils/cn';

interface ToggleSwitchProps {
  label: string;
  path: string;
  hint?: string;
  disabled?: boolean;
  /** For external state management */
  value?: boolean;
  onChange?: (value: boolean) => void;
}

export function ToggleSwitch({
  label,
  path,
  hint: _hint,
  disabled,
  value: externalValue,
  onChange: externalOnChange,
}: ToggleSwitchProps) {
  const { getByPath, setByPath } = useSettings();

  const isExternal = externalValue !== undefined;
  const isOn = isExternal ? externalValue : Boolean(getByPath(path));

  const toggle = useCallback(() => {
    if (disabled) return;
    if (isExternal) {
      externalOnChange?.(!isOn);
    } else {
      setByPath(path, !isOn);
    }
  }, [disabled, isExternal, isOn, externalOnChange, path, setByPath]);

  // v174 (Designer): the label only, the switch on the right; on is blue, off white 20%
  return (
    <div
      className="flex items-center justify-between gap-3 min-h-[44px] py-1 cursor-pointer"
      onClick={toggle}
      role="presentation"
    >
      <span className="text-[13px] font-medium text-white/70 select-none leading-snug">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={isOn}
        aria-label={label}
        onClick={(e) => { e.stopPropagation(); toggle(); }}
        disabled={disabled}
        className={cn(
          'relative h-[28px] w-[50px] shrink-0 rounded-full transition-colors duration-200',
          isOn ? 'bg-[#3b82f6]' : 'bg-white/20',
          disabled && 'opacity-40 cursor-not-allowed',
        )}
      >
        <span
          className={cn(
            'absolute top-[4px] left-[4px] h-[20px] w-[20px] rounded-full bg-white shadow-sm transition-transform duration-200',
            isOn && 'translate-x-[22px]',
          )}
        />
      </button>
    </div>
  );
}

/** v175 (Designer): a card whose only toggle repeats its title shows just the switch, right-aligned in
 * the card header (SettingsCard headerToggle); the duplicate label row is gone. Named by the title. */
export function HeaderSwitch({ label, path, disabled }: { label: string; path: string; disabled?: boolean }) {
  const { getByPath, setByPath } = useSettings();
  const isOn = Boolean(getByPath(path));
  return (
    <button
      type="button"
      role="switch"
      aria-checked={isOn}
      aria-label={label}
      data-testid="card-header-switch"
      onClick={() => { if (!disabled) setByPath(path, !isOn); }}
      disabled={disabled}
      className={cn(
        'relative h-[28px] w-[50px] shrink-0 rounded-full transition-colors duration-200',
        isOn ? 'bg-[#3b82f6]' : 'bg-white/20',
        disabled && 'opacity-40 cursor-not-allowed',
      )}
    >
      <span
        className={cn(
          'absolute top-[4px] left-[4px] h-[20px] w-[20px] rounded-full bg-white shadow-sm transition-transform duration-200',
          isOn && 'translate-x-[22px]',
        )}
      />
    </button>
  );
}
