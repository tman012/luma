/**
 * ChipPicker — multi-select over a fixed set of names, stored as a string array.
 *
 * Used for tracker and genre selection. The convention throughout is that an
 * empty selection means "all of them", not "none" — so the hint has to say so,
 * or an untouched picker reads as a disabled automation.
 */

import { useSettings } from '../useSettings';

export interface Chip {
  name: string;
  /** Rendered dimmed with an "off" marker — e.g. a tracker disabled in Prowlarr. */
  enabled?: boolean;
}

interface ChipPickerProps {
  label: string;
  path: string;
  chips: Chip[];
  /** Shown when nothing is selected. Should make "empty means all" explicit. */
  emptyHint: string;
  /** Given the selected count, describes the active restriction. */
  selectedHint: (count: number) => string;
}

export function ChipPicker({
  label,
  path,
  chips,
  emptyHint,
  selectedHint,
}: ChipPickerProps) {
  const { getByPath, setByPath } = useSettings();
  const raw = getByPath(path);
  const selected = Array.isArray(raw) ? (raw as string[]).map(String) : [];

  const toggle = (name: string) => {
    setByPath(
      path,
      selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name],
    );
  };

  return (
    <div className="col-span-full flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <label className="text-[14px] font-medium text-white/60">{label}</label>
        {selected.length > 0 && (
          <button
            type="button"
            onClick={() => setByPath(path, [])}
            className="text-[11px] font-semibold text-white/40 hover:text-white/70"
          >
            Clear
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {chips.map((c) => {
          const on = selected.includes(c.name);
          return (
            <button
              key={c.name}
              type="button"
              onClick={() => toggle(c.name)}
              className={`min-h-[40px] rounded-lg border px-3 py-2 text-[13px] font-medium transition-colors ${
                on
                  ? 'border-emerald-400/30 bg-emerald-400/15 text-emerald-200'
                  : 'border-white/10 bg-white/5 text-white/45'
              } ${c.enabled === false ? 'opacity-40' : ''}`}
            >
              {c.name}
              {c.enabled === false && (
                <span className="ml-1 text-[10px] text-white/30">off</span>
              )}
            </button>
          );
        })}
      </div>
      <p className="text-[12px] text-white/35">
        {selected.length === 0 ? emptyHint : selectedHint(selected.length)}
      </p>
    </div>
  );
}
