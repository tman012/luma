/** v237: Settings > Home screen > Hero: source and count (owner/admin).
 * Controls use prod 1b4ea74's Settings row styles (FormField: label + INPUT_CLASS). */
import { useEffect, useRef, useState } from 'react';
import { apiFetch } from '@/apiClient';
import { cn } from '@/utils/cn';
import { INPUT_CLASS } from '@/pages/settings/components/legacyTokens';
import { NoticeToast } from '@/components/start/SetupToast';
import { SAVE_FAILED } from '@/utils/messages';
import { FONT } from '@/components/start/StartUI';

export type HeroSource = 'newest' | 'popular';
export interface HeroSettings { source: HeroSource; section_id: string; limit: number; libraries: { id: string; title: string }[];
  excluded_genres: string[]; genres: string[] }
export const HERO_MIN = 5, HERO_MAX = 30;
export const clampCount = (n: number) => Math.max(HERO_MIN, Math.min(HERO_MAX, Math.round(n)));

export async function fetchHero(): Promise<HeroSettings> {
  const r = await apiFetch('/api/settings/hero');
  if (!r.ok) throw new Error(String(r.status));
  const d = await r.json();
  if (!d || (d.source !== 'newest' && d.source !== 'popular')) throw new Error('bad');
  return { ...d, excluded_genres: Array.isArray(d.excluded_genres) ? d.excluded_genres : [], genres: Array.isArray(d.genres) ? d.genres : [] };
}
export async function saveHero(p: Partial<Pick<HeroSettings, 'source' | 'section_id' | 'limit' | 'excluded_genres'>>): Promise<HeroSettings> {
  const r = await apiFetch('/api/settings/hero', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) });
  if (!r.ok) throw new Error(String(r.status));
  const d = await r.json();
  return { ...d, excluded_genres: Array.isArray(d.excluded_genres) ? d.excluded_genres : [], genres: Array.isArray(d.genres) ? d.genres : [] };
}

const SOURCES: { value: HeroSource; label: string }[] = [
  { value: 'newest', label: 'Recently added' },
  { value: 'popular', label: 'Most popular' },
];

export function HeroSettingsRows() {
  const [d, setD] = useState<HeroSettings | null>(null);
  const [count, setCount] = useState('');
  const [toast, setToast] = useState(0);
  const [gOpen, setGOpen] = useState(false);
  const seq = useRef(0);
  useEffect(() => { fetchHero().then(x => { setD(x); setCount(String(x.limit)); }).catch(() => setD(null)); }, []);
  if (!d) return null;
  const save = (p: Parameters<typeof saveHero>[0]) => {
    const before = d;
    setD({ ...d, ...p });
    const n = ++seq.current;
    saveHero(p)
      .then(x => { if (n === seq.current) { setD(x); setCount(String(x.limit)); } })
      .catch(() => { if (n === seq.current) { setD(before); setCount(String(before.limit)); setToast(t => t + 1); } });
  };
  const commitCount = () => {
    const v = Number(count);
    const next = Number.isFinite(v) && count.trim() !== '' ? clampCount(v) : d.limit;
    setCount(String(next));
    if (next !== d.limit) save({ limit: next });
  };
  return (
    <>
    <style>{CHECK_CSS}</style>
    <div data-testid="hero-settings" className="flex flex-col gap-4" style={{ padding: '16px 20px' }}>
      {toast > 0 && <NoticeToast key={toast} text={SAVE_FAILED} onDone={() => setToast(0)} />}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="hero-source" className="text-[14px] font-medium text-white/60">Hero</label>
        <select id="hero-source" data-testid="hero-source" className={cn(INPUT_CLASS, 'cursor-pointer')} value={d.source}
          onChange={e => save({ source: e.target.value as HeroSource })}>
          {SOURCES.map(o => <option key={o.value} value={o.value} className="bg-kiosk-black text-white">{o.label}</option>)}
        </select>
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="hero-count" className="text-[14px] font-medium text-white/60">Number of movies</label>
        <input id="hero-count" data-testid="hero-count" type="number" min={HERO_MIN} max={HERO_MAX} step={1}
          className={INPUT_CLASS} style={{ fontFamily: FONT }} value={count}
          onChange={e => setCount(e.target.value)} onBlur={commitCount}
          onKeyDown={e => { if (e.key === 'Enter') (e.currentTarget as HTMLInputElement).blur(); }} />
      </div>
      <div className="flex flex-col gap-1.5">
        <span id="hero-genres-label" className="text-[14px] font-medium text-white/60">Excluded genres</span>
        <button type="button" data-testid="hero-genres" aria-labelledby="hero-genres-label" aria-expanded={gOpen}
          aria-haspopup="listbox" onClick={() => setGOpen(o => !o)}
          className={cn(INPUT_CLASS, 'cursor-pointer flex items-center justify-between text-left')} style={{ fontFamily: FONT }}>
          <span className="truncate" data-testid="hero-genres-value">{d.excluded_genres.length ? d.excluded_genres.join(', ') : 'None'}</span>
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" style={{ flex: 'none', marginLeft: 8, transform: gOpen ? 'rotate(180deg)' : 'none' }}>
            <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" /></svg>
        </button>
        {gOpen && (
          <div role="listbox" aria-multiselectable="true" aria-labelledby="hero-genres-label" data-testid="hero-genres-list"
            className="grid grid-cols-2 gap-x-4" style={{ padding: '4px 0' }}>
            {d.genres.map(g => {
              const on = d.excluded_genres.some(x => x.toLowerCase() === g.toLowerCase());
              return (
                <label key={g} className="flex items-center gap-2 cursor-pointer text-white/90" style={{ minHeight: 40, fontSize: 15, fontFamily: FONT }}>
                  <input type="checkbox" role="option" aria-selected={on} checked={on} className="luma-check"
                    onChange={() => save({ excluded_genres: on ? d.excluded_genres.filter(x => x.toLowerCase() !== g.toLowerCase()) : [...d.excluded_genres, g] })} />
                  {g}
                </label>
              );
            })}
          </div>
        )}
      </div>
    </div>
    </>
  );
}

/** v241b (Designer): app-styled checkboxes: dark fill, 1px border, #3b82f6 check. */
export const CHECK_CSS = `.luma-check{appearance:none;-webkit-appearance:none;margin:0;flex:none;width:18px;height:18px;border-radius:4px;
  background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.24);display:inline-grid;place-content:center;cursor:pointer}
.luma-check::after{content:"";width:10px;height:6px;border-left:2px solid #3b82f6;border-bottom:2px solid #3b82f6;transform:translateY(-1px) rotate(-45deg);opacity:0}
.luma-check:checked{border-color:#3b82f6}
.luma-check:checked::after{opacity:1}
.luma-check:focus-visible{outline:2px solid #3b82f6;outline-offset:2px}`;
