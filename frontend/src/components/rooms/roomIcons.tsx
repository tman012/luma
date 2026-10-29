/** Settings premium pass: room icons (Designer rooms-v2 24-grid line glyphs), the icon bubble,
 * and the icon picker (Cancel / Done). The room's `icon` is stored on the room (rooms_setup.ROOM_ICONS). */
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { FONT } from '@/components/start/StartUI';

export const ROOM_ICONS = ['sofa', 'bed', 'kitchen', 'dining', 'office', 'theater', 'game', 'kids', 'bath', 'gym',
  'music', 'library', 'patio', 'garage', 'home'] as const;
export type RoomIconId = (typeof ROOM_ICONS)[number];
export const DEFAULT_ROOM_ICON: RoomIconId = 'home';
export const roomIconOf = (v: unknown): RoomIconId => (ROOM_ICONS as readonly string[]).includes(v as string) ? v as RoomIconId : DEFAULT_ROOM_ICON;

const GLYPHS: Record<RoomIconId | 'tv', ReactNode> = {
  sofa: <><path d="M5 11V8.5A2.5 2.5 0 0 1 7.5 6h9A2.5 2.5 0 0 1 19 8.5V11" /><path d="M3.5 11.5A1.5 1.5 0 0 1 6.5 11.5V14h11v-2.5a1.5 1.5 0 0 1 3 0V17a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1z" /><path d="M6 18v1.5M18 18v1.5" /></>,
  bed: <><path d="M3 18V7M3 14h18v4M21 14v-2.5A2.5 2.5 0 0 0 18.5 9H11v5" /><circle cx="7" cy="11" r="1.8" /></>,
  kitchen: <><path d="M4 10h16v2a6 6 0 0 1-6 6h-4a6 6 0 0 1-6-6z" /><path d="M2.5 10H4M20 10h1.5M9 6.5c0-1 1-1 1-2M13 6.5c0-1 1-1 1-2" /></>,
  office: <><rect x="3" y="4.5" width="18" height="11" rx="2" /><path d="M9 20h6M12 15.5V20" /></>,
  game: <><path d="M7 8h10a4 4 0 0 1 3.9 4.8l-.6 3a2.3 2.3 0 0 1-4 1L15 15H9l-1.3 1.8a2.3 2.3 0 0 1-4-1l-.6-3A4 4 0 0 1 7 8z" /><path d="M8 10.5v3M6.5 12h3" /><circle cx="16" cy="11" r=".6" /><circle cx="17.5" cy="13" r=".6" /></>,
  theater: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 9h18M7 5l2 4M12 5l2 4M17 5l2 4" /></>,
  dining: <><path d="M7 3v8M5 3v4a2 2 0 0 0 4 0V3M7 11v10M16 3c-2 1.5-2.5 4-2.5 7H16v11" /></>,
  bath: <><path d="M3.5 12h17v2a5 5 0 0 1-5 5h-7a5 5 0 0 1-5-5z" /><path d="M6 12V6a2 2 0 0 1 3.6-1.2M7 19l-1 2M17 19l1 2" /></>,
  gym: <><path d="M6.5 7v10M17.5 7v10M3.5 9.5v5M20.5 9.5v5M6.5 12h11" /></>,
  patio: <><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" /></>,
  garage: <><path d="M5 16.5V12l2-4.5h10l2 4.5v4.5M3.5 12h17v4.5h-17zM6 16.5v2M18 16.5v2" /><circle cx="7.5" cy="14.2" r=".6" /><circle cx="16.5" cy="14.2" r=".6" /></>,
  kids: <><rect x="3.5" y="12.5" width="7.5" height="7.5" rx="1.5" /><rect x="13" y="12.5" width="7.5" height="7.5" rx="1.5" /><rect x="8.2" y="4" width="7.5" height="7.5" rx="1.5" /></>,
  music: <><path d="M9 18V5.5l11-2V16" /><circle cx="6.5" cy="18" r="2.5" /><circle cx="17.5" cy="16" r="2.5" /></>,
  library: <><path d="M4 19.5V5a1.5 1.5 0 0 1 1.5-1.5H11v16H5.5A1.5 1.5 0 0 0 4 21M20 19.5V5a1.5 1.5 0 0 0-1.5-1.5H13v16h5.5A1.5 1.5 0 0 1 20 21" /></>,
  home: <><path d="M3.5 11L12 4l8.5 7" /><path d="M5.5 9.5V20h13V9.5" /><path d="M10 20v-5.5h4V20" /></>,
  tv: <><rect x="2.5" y="4.5" width="19" height="12.5" rx="2.2" /><path d="M8.5 20.5h7" /></>,
};

export function Glyph({ id, size = 24, color = '#adc7ff', stroke = 1.5 }: { id: RoomIconId | 'tv'; size?: number; color?: string; stroke?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke={color} strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" data-glyph={id}>
      {GLYPHS[id]}
    </svg>
  );
}

/** Round icon bubble: radial #adc7ff 32% → blue 14%, a 1px inner ring. */
export function Bubble({ size, children, style }: { size: number; children: ReactNode; style?: CSSProperties }) {
  return (
    <span style={{ width: size, height: size, borderRadius: '50%', display: 'inline-grid', placeItems: 'center', flex: 'none',
      background: 'radial-gradient(circle at 35% 30%, rgba(173,199,255,.32), rgba(59,130,246,.14) 70%)',
      boxShadow: 'inset 0 0 0 1px rgba(173,199,255,.18)', ...style }}>{children}</span>
  );
}

/** Centered 540px sheet: current icon + room name, 5×3 icon grid, Cancel / Done. */
export function IconPicker({ name, value, onCancel, onDone }: {
  name: string; value: RoomIconId; onCancel: () => void; onDone: (icon: RoomIconId) => void;
}) {
  const [sel, setSel] = useState<RoomIconId>(value);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onCancel(); } };
    document.addEventListener('keydown', k, true);
    return () => document.removeEventListener('keydown', k, true);
  }, [onCancel]);
  return createPortal(
    <div className="luma-fullscreen luma-bleed" data-testid="icon-picker-scrim" onPointerDown={e => { if (e.target === e.currentTarget) onCancel(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 1200, background: 'rgba(10,8,24,.6)', backdropFilter: 'blur(10px)', WebkitBackdropFilter: 'blur(10px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, fontFamily: FONT }}>
      <div role="dialog" aria-modal="true" aria-label={name} data-testid="icon-picker"
        style={{ width: 540, maxWidth: '100%', boxSizing: 'border-box', padding: '36px 40px 30px', borderRadius: 28, textAlign: 'center',
          display: 'flex', flexDirection: 'column', alignItems: 'center', color: 'rgba(255,255,255,0.95)',
          background: 'linear-gradient(180deg, rgba(34,28,70,.98), rgba(24,19,50,.98))', border: '1px solid rgba(255,255,255,0.1)', boxShadow: '0 40px 100px rgba(0,0,0,.6)' }}>
        <Bubble size={104}><Glyph id={sel} size={52} /></Bubble>
        <div style={{ fontSize: 22, fontWeight: 650, margin: '16px 0 26px' }}>{name}</div>
        <div role="radiogroup" aria-label="Icon" style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 64px)', gap: 18, justifyContent: 'center' }}>
          {ROOM_ICONS.map(k => {
            const on = k === sel;
            return (
              <button key={k} type="button" role="radio" aria-checked={on} aria-label={k[0]!.toUpperCase() + k.slice(1)} data-testid={`icon-${k}`} onClick={() => setSel(k)}
                style={{ width: 64, height: 64, borderRadius: '50%', border: 'none', padding: 0, cursor: 'pointer', display: 'grid', placeItems: 'center',
                  background: on ? 'radial-gradient(circle at 35% 30%, rgba(173,199,255,.32), rgba(59,130,246,.14) 70%)' : 'rgba(255,255,255,0.07)',
                  boxShadow: on ? '0 0 0 3px #3b82f6, 0 0 20px rgba(59,130,246,.5)' : 'none' }}>
                <Glyph id={k} size={28} color={on ? '#fff' : 'rgba(255,255,255,0.75)'} />
              </button>
            );
          })}
        </div>
        <div style={{ display: 'flex', gap: 12, justifyContent: 'center', marginTop: 30 }}>
          <button type="button" data-testid="icon-picker-cancel" onClick={onCancel} style={{ ...pill, background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.12)' }}>Cancel</button>
          <button type="button" data-testid="icon-picker-done" onClick={() => onDone(sel)} style={{ ...pill, background: '#3b82f6', boxShadow: '0 8px 24px rgba(59,130,246,.35)' }}>Done</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

const pill: CSSProperties = {
  height: 52, minWidth: 140, padding: '0 28px', borderRadius: 999, border: 'none', color: '#fff', fontSize: 16, fontWeight: 600,
  fontFamily: FONT, cursor: 'pointer',
};
