/**
 * v173 (PM): the global search settings, moved here from Explore's old indexer card
 * (settings tracker_search.*; values migrated from indexer.*). Saved on blur.
 */
import { useEffect, useState } from 'react';
import type React from 'react';
import { fetchSettings, saveSettings } from '@/services/settingsApi';
import { SAVE_FAILED } from '@/utils/messages';
import { DANGER_TEXT, FONT, GLASS, GLASS_BORDER, TEXT, TEXT_2 } from './trackersUi';

export const SEARCH_TITLE = 'Search';
export type SearchKey = 'movie_categories' | 'max_age_days' | 'min_seeders' | 'limit';

interface FieldDef { key: SearchKey; label: string; numeric: boolean; min?: number; max?: number }

export const SEARCH_FIELDS: FieldDef[] = [
  { key: 'movie_categories', label: 'Movie categories', numeric: false },
  { key: 'max_age_days', label: 'Max age (days)', numeric: true, min: 1, max: 365 },
  { key: 'min_seeders', label: 'Minimum seeders', numeric: true, min: 0 },
  { key: 'limit', label: 'Result limit', numeric: true, min: 10, max: 500 },
];

/** The error for a value, or '' when it can be saved. */
export function searchFieldError(f: FieldDef, raw: string): string {
  const v = raw.trim();
  if (!f.numeric) {
    return /^\d+(\s*,\s*\d+)*$/.test(v) ? '' : 'Enter category numbers separated by commas';
  }
  if (!/^\d+$/.test(v)) return 'Enter a whole number';
  const n = Number(v);
  if (f.min !== undefined && n < f.min) return f.max !== undefined ? `Enter ${f.min} to ${f.max}` : `Enter ${f.min} or more`;
  if (f.max !== undefined && n > f.max) return `Enter ${f.min ?? 0} to ${f.max}`;
  return '';
}

type Values = Record<SearchKey, string>;

export function SearchSettings({ wide, onSaved }: { wide: boolean; onSaved: (text: string) => void }) {
  const [saved, setSaved] = useState<Values | null>(null);
  const [values, setValues] = useState<Values | null>(null);
  const [errors, setErrors] = useState<Partial<Record<SearchKey, string>>>({});

  useEffect(() => {
    let alive = true;
    fetchSettings().then(s => {
      const ts = (s.tracker_search || {}) as Record<string, unknown>;
      const v = Object.fromEntries(SEARCH_FIELDS.map(f => [f.key, ts[f.key] === undefined || ts[f.key] === null ? '' : String(ts[f.key])])) as Values;
      if (alive) { setSaved(v); setValues(v); }
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  if (!values || !saved) return null;

  const commit = async (f: FieldDef) => {
    const raw = values[f.key].trim();
    if (raw === saved[f.key]) { setErrors(e => ({ ...e, [f.key]: '' })); return; }
    const err = searchFieldError(f, raw);
    setErrors(e => ({ ...e, [f.key]: err }));
    if (err) return;
    const value = f.numeric ? Number(raw) : raw.split(',').map(x => x.trim()).join(',');
    const r = await saveSettings({ tracker_search: { [f.key]: value } }).catch(() => ({ success: false }));
    if (r.success) {
      const shown = String(value);
      setSaved(s => (s ? { ...s, [f.key]: shown } : s));
      setValues(v => (v ? { ...v, [f.key]: shown } : v));
      onSaved('Saved');
    } else {
      onSaved(SAVE_FAILED);
    }
  };

  const label: React.CSSProperties = { display: 'block', margin: '0 0 6px', fontSize: 13, fontWeight: 600, color: TEXT_2, fontFamily: FONT };
  const fields = SEARCH_FIELDS.map(f => {
    const id = `tracker-search-${f.key}`;
    const err = errors[f.key] || '';
    return (
      <div key={f.key}>
        <label htmlFor={id} style={label}>{f.label}</label>
        <input id={id} data-testid={id} className="lt-input" autoComplete="off" spellCheck={false}
          type="text" inputMode={f.numeric ? 'numeric' : 'text'}
          aria-invalid={err ? true : undefined} aria-describedby={err ? `${id}-error` : undefined}
          value={values[f.key]} onChange={e => setValues(v => (v ? { ...v, [f.key]: e.target.value } : v))}
          onBlur={() => commit(f)} />
        {err && <span id={`${id}-error`} data-testid={`${id}-error`} style={{ display: 'block', marginTop: 6, fontSize: 13, color: DANGER_TEXT }}>{err}</span>}
      </div>
    );
  });

  return (
    <div data-testid="tracker-search-settings" style={{ marginTop: wide ? 32 : 24, padding: wide ? 0 : '0 20px' }}>
      <h2 style={{ margin: '0 0 12px', fontSize: wide ? 17 : 15, fontWeight: 600, color: TEXT, fontFamily: FONT }}>{SEARCH_TITLE}</h2>
      <div style={{ display: 'grid', gridTemplateColumns: wide ? 'repeat(2, minmax(0, 1fr))' : '1fr', gap: 16,
        padding: wide ? 20 : 16, borderRadius: 16, background: GLASS, border: GLASS_BORDER }}>
        {fields}
      </div>
    </div>
  );
}
