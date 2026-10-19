/** v185 (Taylor): Sonarr-style episode naming (Settings > TV > Options), seeded from his Sonarr
 * NamingConfig. A live example sits under each format field; a format saves when it's valid and you
 * leave the field; the switches and menus save at once. Only files Luma imports get the new names. */
import { useEffect, useRef, useState } from 'react';
import { Row, Select, Toggle, field } from './TvEdit';
import { card, sectionLabel } from './LibraryPathsSettings';
import { apiFetch } from '@/apiClient';
import { TV_URL } from './tvLibraryApi';

export type Naming = {
  rename_episodes: boolean; standard: string; daily: string; anime: string; series_folder: string; season_folder: string;
  specials_folder: string; multi_episode_style: string; colon_replacement: string; replace_illegal: boolean;
};
type Examples = Partial<Record<keyof Naming, string>>;
type Errors = Record<string, string>;

export const FORMAT_FIELDS: { key: keyof Naming; label: string }[] = [
  { key: 'standard', label: 'Standard episode format' }, { key: 'daily', label: 'Daily episode format' },
  { key: 'anime', label: 'Anime episode format' }, { key: 'series_folder', label: 'Series folder format' },
  { key: 'season_folder', label: 'Season folder format' }, { key: 'specials_folder', label: 'Specials folder format' },
];
export const MULTI_STYLES = [
  { id: 'extend', label: 'Extend' }, { id: 'duplicate', label: 'Duplicate' }, { id: 'repeat', label: 'Repeat' },
  { id: 'scene', label: 'Scene' }, { id: 'range', label: 'Range' }, { id: 'prefixed_range', label: 'Prefixed range' },
];
export const COLONS = [
  { id: 'delete', label: 'Delete' }, { id: 'dash', label: 'Replace with dash' }, { id: 'space_dash', label: 'Replace with space dash' },
  { id: 'space_dash_space', label: 'Replace with space dash space' }, { id: 'smart', label: 'Smart replace' },
];

const W50 = 'rgba(255,255,255,0.5)';
const RED = '#f87171';
const example: React.CSSProperties = { marginTop: 6, fontSize: 13, lineHeight: '18px', color: W50, overflowWrap: 'anywhere' };

async function post(path: string, method: string, body: unknown): Promise<{ ok: boolean; data: Record<string, unknown> }> {
  try {
    const r = await apiFetch(`${TV_URL}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return { ok: r.ok, data: await r.json().catch(() => ({})) as Record<string, unknown> };
  } catch {
    return { ok: false, data: {} };
  }
}

export function NamingSettings({ value, onSaved, say }: { value: Naming; onSaved: (n: Naming) => void; say: (m: string) => void }) {
  const [draft, setDraft] = useState<Naming>(value);
  const [examples, setExamples] = useState<Examples>({});
  const [errors, setErrors] = useState<Errors>({});
  const seq = useRef(0);
  useEffect(() => { setDraft(value); }, [value]);
  useEffect(() => {
    const n = ++seq.current;
    const t = setTimeout(() => {
      void post('/naming/preview', 'POST', { naming: draft }).then(r => {
        if (n !== seq.current || !r.ok) return;
        setExamples((r.data.examples || {}) as Examples);
        setErrors((r.data.errors || {}) as Errors);
      });
    }, 200);
    return () => clearTimeout(t);
  }, [draft]);

  const save = (part: Partial<Naming>) => {
    void post('', 'PUT', { naming: part }).then(r => {
      const n = (r.data as { naming?: Naming }).naming;
      if (r.ok && n) onSaved(n);
      else if (!r.ok) {
        const errs = (r.data.errors || {}) as Errors;
        if (!Object.keys(errs).length) say("Couldn't save");
      }
    });
  };
  const set = (part: Partial<Naming>, now: boolean) => { setDraft(d => ({ ...d, ...part })); if (now) save(part); };

  const Example = ({ k }: { k: keyof Naming }) => {
    const err = errors[`naming.${k}`];
    if (err) return <div role="alert" data-testid={`tv-naming-${k}-error`} style={{ ...example, color: RED }}>{err}</div>;
    return <div data-testid={`tv-naming-${k}-example`} style={example}>{examples[k] || '\u00a0'}</div>;
  };

  return (
    <div data-testid="tv-naming">
      <h3 style={sectionLabel}>Episode naming</h3>
      <div style={{ ...card, overflow: 'visible', padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Row label="Rename episodes" id="tv-naming-rename">
          <Toggle id="tv-naming-rename" on={draft.rename_episodes} onChange={on => set({ rename_episodes: on }, true)} />
        </Row>
        {FORMAT_FIELDS.map(f => (
          <Row key={f.key} label={f.label} id={`tv-naming-${f.key}`}>
            <input id={`tv-naming-${f.key}`} className="lp-focus" data-testid={`tv-naming-${f.key}`} value={String(draft[f.key])} spellCheck={false}
              autoComplete="off" aria-invalid={!!errors[`naming.${f.key}`] || undefined}
              onChange={e => set({ [f.key]: e.target.value } as Partial<Naming>, false)}
              onBlur={() => { if (draft[f.key] !== value[f.key] && !errors[`naming.${f.key}`]) save({ [f.key]: draft[f.key] } as Partial<Naming>); }}
              style={{ ...field, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 13 }} />
            <Example k={f.key} />
          </Row>
        ))}
        <Row label="Multi-episode style" id="tv-naming-multi">
          <Select id="tv-naming-multi" value={draft.multi_episode_style} options={MULTI_STYLES} onChange={v => set({ multi_episode_style: v }, true)} />
          <Example k="multi_episode_style" />
        </Row>
        <Row label="Colon replacement" id="tv-naming-colon">
          <Select id="tv-naming-colon" value={draft.colon_replacement} options={COLONS} onChange={v => set({ colon_replacement: v }, true)} />
        </Row>
        <Row label="Replace illegal characters" id="tv-naming-illegal">
          <Toggle id="tv-naming-illegal" on={draft.replace_illegal} onChange={on => set({ replace_illegal: on }, true)} />
        </Row>
      </div>
    </div>
  );
}
