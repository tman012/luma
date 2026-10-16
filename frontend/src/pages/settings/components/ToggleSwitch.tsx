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
  hint,
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

  return (
    <div
      className="flex items-center gap-3 min-h-[44px] py-1 cursor-pointer"
      onClick={toggle}
      role="presentation"
    >
      <button
        type="button"
        role="switch"
        aria-checked={isOn}
        onClick={(e) => { e.stopPropagation(); toggle(); }}
        disabled={disabled}
        className={cn(
          'relative h-[28px] w-[50px] shrink-0 rounded-full transition-colors duration-200',
          isOn ? 'bg-emerald-500' : 'bg-white/15',
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
      <div className="flex flex-col flex-1">
        <span className="text-[14px] font-medium text-white/70 select-none leading-snug">
          {label}
        </span>
        {hint && (
          <span className="text-[11px] text-white/30 mt-0.5">{hint}</span>
        )}
      </div>
    </div>
  );
}
