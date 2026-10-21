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
export interface HeroSettings { source: HeroSource; section_id: string; limit: number; libraries: { id: string; title: string }[] }
export const HERO_MIN = 5, HERO_MAX = 30;
export const clampCount = (n: number) => Math.max(HERO_MIN, Math.min(HERO_MAX, Math.round(n)));

export async function fetchHero(): Promise<HeroSettings> {
  const r = await apiFetch('/api/settings/hero');
  if (!r.ok) throw new Error(String(r.status));
  const d = await r.json();
  if (!d || (d.source !== 'newest' && d.source !== 'popular')) throw new Error('bad');
  return d;
}
export async function saveHero(p: Partial<Pick<HeroSettings, 'source' | 'section_id' | 'limit'>>): Promise<HeroSettings> {
  const r = await apiFetch('/api/settings/hero', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

const SOURCES: { value: HeroSource; label: string }[] = [
  { value: 'newest', label: 'Recently added' },
  { value: 'popular', label: 'Most popular' },
];

export function HeroSettingsRows() {
  const [d, setD] = useState<HeroSettings | null>(null);
  const [count, setCount] = useState('');
  const [toast, setToast] = useState(0);
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
    </div>
  );
}
