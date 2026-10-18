/**
 * FormField — input/select/password field bound to a settings dot-path.
 */

import { useCallback, useId } from 'react';
import { useSettings } from '../useSettings';
import { cn } from '@/utils/cn';
import { isMasked } from '@/services/settingsApi';
import { INPUT_CLASS } from './legacyTokens';

interface Option {
  value: string | number;
  label: string;
}

interface FormFieldProps {
  label: string;
  path: string;
  type?: 'text' | 'password' | 'number' | 'select' | 'time';
  placeholder?: string;
  hint?: string;
  options?: Option[];
  min?: number;
  max?: number;
  step?: number;
  maxLength?: number;
  disabled?: boolean;
  className?: string;
  /** For external state management (not bound to context) */
  value?: string | number;
  onChange?: (value: string | number) => void;
}

const inputClass = INPUT_CLASS;

export function FormField({
  label,
  path,
  type = 'text',
  placeholder,
  hint: _hint,
  options,
  min,
  max,
  step,
  maxLength,
  disabled,
  className,
  value: externalValue,
  onChange: externalOnChange,
}: FormFieldProps) {
  const { getByPath, setByPath } = useSettings();
  const id = useId();

  const isExternal = externalValue !== undefined;
  const rawValue = isExternal ? externalValue : getByPath(path);
  const displayValue = isMasked(rawValue) ? '' : (rawValue ?? '');

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      let val: string | number = e.target.value;
      if (type === 'number' && val !== '') {
        val = Number(val);
      }
      if (isExternal) {
        externalOnChange?.(val);
      } else {
        setByPath(path, val);
      }
    },
    [type, path, setByPath, isExternal, externalOnChange],
  );

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={id} className="text-[13px] font-medium text-white/70">{label}</label>
      {type === 'select' ? (
        <select
          id={id}
          value={String(displayValue)}
          onChange={handleChange}
          disabled={disabled}
          className={cn(inputClass, 'cursor-pointer')}
        >
          {options?.map((opt) => (
            <option key={opt.value} value={opt.value} className="bg-kiosk-black text-white">
              {opt.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          type={
            type === 'password' ? 'password'
              : type === 'number' ? 'number'
              : type === 'time' ? 'time'
              : 'text'
          }
          value={String(displayValue)}
          onChange={handleChange}
          placeholder={isMasked(rawValue) ? '********' : placeholder}
          disabled={disabled}
          min={min}
          max={max}
          step={step}
          maxLength={maxLength}
          className={cn(inputClass, false)}
        />
      )}
    </div>
  );
}
