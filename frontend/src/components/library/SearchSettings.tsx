/** v256 (PM, arr-style searching): the Search sections of Settings > TV > Options and Settings > Movies,
 * in their glass-card row style. TV: Air-time searches and Slow sweep for missing episodes (toggles), On add
 * (Search now | Wait for sweep). Movies: Availability (Announced | In cinemas | Released). */
import type { CSSProperties } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Toggle } from './TvEdit';
import { card, sectionLabel, segBtn, segOn, segment } from './LibraryPathsSettings';
import type { OnAdd, TvAutomation } from './tvLibraryApi';

const W95 = 'rgba(255,255,255,0.95)';
const row = (i: number): CSSProperties => ({
  minHeight: 56, display: 'flex', alignItems: 'center', flexWrap: 'wrap', columnGap: 12, rowGap: 8, padding: '10px 16px',
  boxSizing: 'border-box', borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none', fontFamily: FONT,
});
const label: CSSProperties = { flex: '1 1 160px', minWidth: 0, fontSize: 14, fontWeight: 500, color: W95 };

export const ON_ADD_OPTIONS: { id: OnAdd; label: string }[] = [
  { id: 'search_now', label: 'Search now' }, { id: 'wait_for_sweep', label: 'Wait for sweep' },
];
export type Availability = 'announced' | 'in_cinemas' | 'released';
export const AVAILABILITY_OPTIONS: { id: Availability; label: string }[] = [
  { id: 'announced', label: 'Announced' }, { id: 'in_cinemas', label: 'In cinemas' }, { id: 'released', label: 'Released' },
];

function Choice<V extends string>({ name, value, options, onChange, testid }: {
  name: string; value: V; options: { id: V; label: string }[]; onChange: (v: V) => void; testid: string;
}) {
  return (
    <div role="radiogroup" aria-label={name} data-testid={testid}
      style={{ ...segment, flex: '1 1 240px', maxWidth: 360, minWidth: 0, boxSizing: 'border-box' }}>
      {options.map(o => {
        const on = o.id === value;
        return (
          <button key={o.id} type="button" role="radio" aria-checked={on} className="lp-focus" data-testid={`${testid}-${o.id}`}
            onClick={() => { if (!on) onChange(o.id); }}
            style={{ ...segBtn, ...(on ? segOn : null), height: 32, fontSize: 14, padding: '0 8px', whiteSpace: 'nowrap', minWidth: 0 }}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function TvSearchSettings({ value, onChange }: { value: TvAutomation; onChange: (patch: Partial<TvAutomation>) => void }) {
  const air = value.air_searches !== false;
  const sweep = value.sweep_enabled !== false;
  const onAdd: OnAdd = value.on_add === 'wait_for_sweep' ? 'wait_for_sweep' : 'search_now';
  return (
    <div data-testid="tv-search-settings">
      <h3 style={sectionLabel}>Search</h3>
      <div style={card}>
        <div style={row(0)}>
          <span id="tv-air-searches-label" style={label}>Air-time searches</span>
          <Toggle id="tv-air-searches" label="Air-time searches" on={air} onChange={v => onChange({ air_searches: v })} />
        </div>
        <div style={row(1)}>
          <span id="tv-sweep-label" style={label}>Slow sweep for missing episodes</span>
          <Toggle id="tv-sweep" label="Slow sweep for missing episodes" on={sweep} onChange={v => onChange({ sweep_enabled: v })} />
        </div>
        <div style={row(2)}>
          <span id="tv-on-add-label" style={label}>On add</span>
          <Choice name="On add" value={onAdd} options={ON_ADD_OPTIONS} testid="tv-on-add" onChange={v => onChange({ on_add: v })} />
        </div>
      </div>
    </div>
  );
}

export function MovieSearchSettings({ value, onChange }: { value: Availability; onChange: (v: Availability) => void }) {
  return (
    <div data-testid="movie-search-settings">
      <h3 style={sectionLabel}>Search</h3>
      <div style={card}>
        <div style={row(0)}>
          <span id="mv-availability-label" style={label}>Availability</span>
          <Choice name="Availability" value={value} options={AVAILABILITY_OPTIONS} testid="mv-availability" onChange={onChange} />
        </div>
      </div>
    </div>
  );
}
