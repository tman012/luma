/**
 * v173 (PM): the global search settings, moved here from Explore's old indexer card
 * (settings tracker_search.*; values migrated from indexer.*). Saved on blur.
 * v174 (PM): only Movie categories, the one wired setting (max age, minimum seeders and result
 * limit were never used by any search and are gone). Reads and writes /api/trackers/search-settings
 * (owner only, validated: known movie categories only); the luma-test build serves no /api/settings.
 */
import { useEffect, useState } from 'react';
import type React from 'react';
import { fetchSearchSettings, saveSearchSettings } from './trackersApi';
import { SAVE_FAILED } from '@/utils/messages';
import { DANGER_TEXT, FONT, GLASS, GLASS_BORDER, TEXT, TEXT_2 } from './trackersUi';

export const SEARCH_TITLE = 'Search';
export type SearchKey = 'movie_categories';

interface FieldDef { key: SearchKey; label: string; numeric: boolean; min?: number; max?: number }

export const SEARCH_FIELDS: FieldDef[] = [
  { key: 'movie_categories', label: 'Movie categories', numeric: false },
];

export const CATEGORIES_ERROR = 'Enter movie category numbers separated by commas';
export const CATEGORIES_MAX = 80;
/** Newznab movie categories (the server's list wins once loaded). */
export const KNOWN_CATEGORIES = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080];

/** The error for a value, or '' when it can be saved. */
export function searchFieldError(f: FieldDef, raw: string, known: number[] = KNOWN_CATEGORIES): string {
  const v = raw.trim();
  if (!f.numeric) {
    if (v.length > CATEGORIES_MAX || !/^\d{1,6}(\s*,\s*\d{1,6})*$/.test(v)) return CATEGORIES_ERROR;
    return v.split(',').every(x => known.includes(Number(x.trim()))) ? '' : CATEGORIES_ERROR;
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
  const [known, setKnown] = useState<number[]>(KNOWN_CATEGORIES);

  useEffect(() => {
    let alive = true;
    fetchSearchSettings().then(r => {
      if (!alive || !r.ok) return;
      const v = { movie_categories: typeof r.data.movie_categories === 'string' ? r.data.movie_categories : '' };
      if (Array.isArray(r.data.known_categories) && r.data.known_categories.length) setKnown(r.data.known_categories);
      setSaved(v); setValues(v);
    });
    return () => { alive = false; };
  }, []);

  if (!values || !saved) return null;

  const commit = async (f: FieldDef) => {
    const raw = values[f.key].trim();
    if (raw === saved[f.key]) { setErrors(e => ({ ...e, [f.key]: '' })); return; }
    const err = searchFieldError(f, raw, known);
    setErrors(e => ({ ...e, [f.key]: err }));
    if (err) return;
    const value = raw.split(',').map(x => x.trim()).join(',');
    const r = await saveSearchSettings({ movie_categories: value });
    if (r.ok) {
      const shown = typeof r.data.movie_categories === 'string' ? r.data.movie_categories : value;
      setSaved(s => (s ? { ...s, [f.key]: shown } : s));
      setValues(v => (v ? { ...v, [f.key]: shown } : v));
      onSaved('Saved');
    } else if (r.status === 400 && r.data.fields?.movie_categories) {
      setErrors(e => ({ ...e, [f.key]: CATEGORIES_ERROR }));
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
          type="text" inputMode={f.numeric ? 'numeric' : 'text'} maxLength={CATEGORIES_MAX}
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
      <div style={{ display: 'grid', gridTemplateColumns: wide ? 'minmax(0, 360px)' : '1fr', gap: 16,
        padding: wide ? 20 : 16, borderRadius: 16, background: GLASS, border: GLASS_BORDER }}>
        {fields}
      </div>
    </div>
  );
}
